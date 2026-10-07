"""Prepare the pinned native chat engine for development; the portable handles this itself."""
import hashlib
import json
import os
import sys
import threading
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from macbot.setup import download_file


def prepare():
    manifest = json.loads((ROOT / "bootstrap/engines.json").read_text())
    engine = next(item for item in manifest["components"] if item["name"] == "llama")
    folder = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MacBot/Development/llama"
    folder.mkdir(parents=True, exist_ok=True)
    def matches(record):
        path = folder / record["path"]
        if not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("The engine manifest contains an invalid path.")
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == record["sha256"]
    if all(matches(record) for record in engine["files"]):
        return
    archive = folder.parent / "cache" / f"llama-{engine['version']}.zip"
    download_file(engine["url"], archive, engine["bytes"], engine["sha256"],
                  lambda *_: None, threading.Event())
    with zipfile.ZipFile(archive) as zipped:
        for record in engine["files"]:
            target = folder / record["path"]
            if not target.resolve().is_relative_to(folder.resolve()):
                raise ValueError("The engine manifest contains an invalid path.")
            data = zipped.read(engine["prefix"] + record["path"])
            if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("An engine file failed its integrity check.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    archive.unlink()


if __name__ == "__main__":
    prepare()
    print("Local chat engine ready.")
