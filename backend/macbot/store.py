import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "uploads").mkdir(exist_ok=True)
        (directory / "artifacts").mkdir(exist_ok=True)
        for folder in ("logs", "models", "workspace"):
            (directory / folder).mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(directory / "macbot.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY, title TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, chat_id TEXT, role TEXT,
                content TEXT, sources TEXT DEFAULT '[]', created TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, chat_id TEXT, kind TEXT,
                status TEXT, request TEXT, result TEXT DEFAULT '{}', created TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, name TEXT, kind TEXT, text TEXT, path TEXT);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS message_uploads(message_id TEXT, upload_id TEXT,
                PRIMARY KEY(message_id,upload_id));
            CREATE TABLE IF NOT EXISTS index_state(upload_id TEXT PRIMARY KEY, chunks INTEGER, error TEXT);
            CREATE TABLE IF NOT EXISTS upload_details(upload_id TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, message_id TEXT, chat_id TEXT,
                kind TEXT, name TEXT, path TEXT, metadata TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS mcp_servers(id TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS mcp_calls(id TEXT PRIMARY KEY, job_id TEXT, status TEXT, result TEXT);
        """)
        self.db.execute(
            "UPDATE jobs SET status='interrupted', result=? WHERE status IN ('queued','running')",
            (json.dumps({"error": "MacBot restarted. You can resume this task."}),),
        )
        self.db.commit()
        for table, folder in (("uploads", "uploads"), ("artifacts", "artifacts")):
            for row in self.db.execute(f"SELECT id,path FROM {table}").fetchall():
                filename = Path(row["path"]).name
                target = (directory / folder / filename).resolve()
                if filename and target.is_relative_to((directory / folder).resolve()):
                    self.db.execute(f"UPDATE {table} SET path=? WHERE id=?", (str(target), row["id"]))
        self.db.commit()

        if self.setting("persona_training", {}).get("state") == "running":
            self.set_setting("persona_training", {"state": "interrupted", "error": "MacBot closed during training. Start a new session."})

    def execute(self, sql, args=()):
        with self.lock:
            cursor = self.db.execute(sql, args)
            rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
            self.db.commit()
            return rows

    def chats(self):
        return self.execute("SELECT * FROM chats ORDER BY created DESC")

    def create_chat(self, title="New conversation"):
        chat = {"id": uuid4().hex, "title": title[:90], "created": now()}
        self.execute("INSERT INTO chats VALUES(:id,:title,:created)", chat)
        return chat

    def messages(self, chat_id):
        rows = self.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY created", (chat_id,))
        for row in rows:
            row["sources"] = json.loads(row["sources"])
            row["artifacts"] = self.execute("SELECT id,kind,name,metadata FROM artifacts WHERE message_id=? ORDER BY created", (row["id"],))
            for artifact in row["artifacts"]:
                artifact["metadata"] = json.loads(artifact["metadata"])
        return rows

    def message(self, chat_id, role, content, sources=None, artifacts=None):
        mid = uuid4().hex
        self.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?)",
            (mid, chat_id, role, content, json.dumps(sources or []), now()),
        )
        for artifact in artifacts or []:
            self.execute("INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?,?,?,?,?)",
                         (artifact["id"], mid, chat_id, artifact["kind"], artifact["name"], artifact["path"],
                          json.dumps(artifact.get("metadata", {})), now()))
        if role == "user" and len(self.messages(chat_id)) == 1:
            self.execute("UPDATE chats SET title=? WHERE id=?", (content[:70], chat_id))
        return mid

    def setting(self, key: str, default: Any = None) -> Any:
        rows = self.execute("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key, value):
        self.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def event(self, job_id, data):
        self.execute("INSERT INTO events(job_id,data) VALUES(?,?)", (job_id, json.dumps(data)))

    def job(self, job_id):
        rows = self.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not rows:
            raise KeyError(job_id)
        row = rows[0]
        row["request"] = json.loads(row["request"])
        row["result"] = json.loads(row["result"])
        return row

    def finish_job(self, job_id, chat_id, result):
        mid = uuid5(NAMESPACE_URL, "macbot-message:" + job_id).hex
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?)",
                                (mid, chat_id, "assistant", result["answer"], json.dumps(result.get("sources", [])), now()))
                for artifact in result.get("artifacts", []):
                    artifact["path"] = str(self.directory / "artifacts" / Path(artifact["path"]).name)
                    self.db.execute("INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?,?,?,?,?)",
                        (artifact["id"], mid, chat_id, artifact["kind"], artifact["name"], artifact["path"], json.dumps(artifact.get("metadata", {})), now()))
                self.db.execute("UPDATE jobs SET status='completed',result=? WHERE id=?", (json.dumps({"message_id": mid}), job_id))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return mid


def data_directory():
    return Path(os.environ.get("MACBOT_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
