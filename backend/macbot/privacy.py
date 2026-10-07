import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class EraseRequest(BaseModel):
    confirmation: str


def remove_owned(root, path):
    root, path = root.resolve(), Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(root) or path.resolve() == root:
        raise ValueError("The deletion target is outside this MacBot workspace.")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def privacy_routes(service):
    router = APIRouter(prefix="/api")
    root = service.store.directory

    @router.get("/privacy")
    async def inventory():
        return {"directory": str(root), "conversations": len(service.store.chats()),
                "attachments": service.store.execute("SELECT COUNT(*) AS n FROM uploads")[0]["n"],
                "telemetry": False, "history_encrypted": False,
                "network_features": ["First-run model downloads", "Deep Research web searches", "Connections you enable", "Links you open"]}

    @router.post("/privacy/erase")
    async def erase(value: EraseRequest):
        if value.confirmation != "ERASE MY WORKSPACE":
            raise HTTPException(400, "Type ERASE MY WORKSPACE to confirm.")
        if service.tasks:
            raise HTTPException(409, "Stop all active tasks and downloads before erasing your workspace.")
        async with service.work_lock:
            await asyncio.to_thread(service.retrieval.release_models)
            service.retrieval.close()
            for name in ("uploads", "artifacts", "workspace", "qdrant", "models/persona-adapter"):
                await asyncio.to_thread(remove_owned, root, root / name)
            for name in ("uploads", "artifacts", "workspace"):
                (root / name).mkdir(parents=True, exist_ok=True)
            for path in root.glob("checkpoints.sqlite3*"):
                remove_owned(root, path)
            with service.store.lock:
                for table in ("message_uploads", "upload_details", "index_state", "events", "artifacts", "messages", "jobs", "chats", "uploads", "mcp_calls", "mcp_servers"):
                    service.store.db.execute(f"DELETE FROM {table}")
                service.store.db.execute("DELETE FROM settings WHERE key != 'settings'")
                service.store.db.commit()
                service.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                service.store.db.execute("VACUUM")
            from .mcp_client import add_workspace
            add_workspace(service.store)
        return {"erased": True, "models_kept": True, "note": "Public model downloads were kept. Runtime logs and backups may retain filenames; this is not a secure disk erase."}

    @router.delete("/chats/{identifier}")
    async def delete_chat(identifier: str):
        if service.tasks:
            raise HTTPException(409, "Finish active tasks before deleting a conversation.")
        if not service.store.execute("SELECT id FROM chats WHERE id=?", (identifier,)):
            raise HTTPException(404, "Conversation not found.")
        async with service.work_lock:
            uploads = service.store.execute("SELECT DISTINCT mu.upload_id AS id FROM message_uploads mu JOIN messages m ON m.id=mu.message_id WHERE m.chat_id=?", (identifier,))
            orphaned = [row["id"] for row in uploads if not service.store.execute(
                "SELECT 1 FROM message_uploads mu JOIN messages m ON m.id=mu.message_id WHERE mu.upload_id=? AND m.chat_id!=?", (row["id"], identifier))]
            await asyncio.to_thread(service.retrieval.delete_uploads, orphaned)
            for artifact in service.store.execute("SELECT path FROM artifacts WHERE chat_id=?", (identifier,)):
                remove_owned(root, artifact["path"])
            for message in service.store.execute("SELECT id FROM messages WHERE chat_id=?", (identifier,)):
                remove_owned(root, root / "artifacts" / f"voice-{message['id']}.wav")
                for extension in ("md", "txt", "docx", "pdf"):
                    remove_owned(root, root / "artifacts" / f"macbot-{message['id']}.{extension}")
            for upload_id in orphaned:
                for upload in service.store.execute("SELECT path FROM uploads WHERE id=?", (upload_id,)):
                    remove_owned(root, upload["path"])
            jobs = service.store.execute("SELECT id FROM jobs WHERE chat_id=?", (identifier,))
            if (root / "checkpoints.sqlite3").exists():
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
                async with AsyncSqliteSaver.from_conn_string(str(root / "checkpoints.sqlite3")) as checkpointer:
                    for job in jobs:
                        await checkpointer.adelete_thread(job["id"])
            with service.store.lock:
                for job in jobs:
                    service.store.db.execute("DELETE FROM events WHERE job_id=?", (job["id"],))
                    service.store.db.execute("DELETE FROM mcp_calls WHERE job_id=?", (job["id"],))
                for upload_id in orphaned:
                    for table, column in (("uploads", "id"), ("upload_details", "upload_id"), ("index_state", "upload_id")):
                        service.store.db.execute(f"DELETE FROM {table} WHERE {column}=?", (upload_id,))
                service.store.db.execute("DELETE FROM message_uploads WHERE message_id IN (SELECT id FROM messages WHERE chat_id=?)", (identifier,))
                for table in ("artifacts", "messages", "jobs"):
                    service.store.db.execute(f"DELETE FROM {table} WHERE chat_id=?", (identifier,))
                service.store.db.execute("DELETE FROM chats WHERE id=?", (identifier,))
                service.store.db.commit()
                service.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                service.store.db.execute("VACUUM")
        return {"deleted": True}
    return router
