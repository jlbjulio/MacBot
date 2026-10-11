"""Prepare the pinned native chat engine for development; the portable handles this itself."""
import hashlib
import json
import os
import shutil
import sys
import threading
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from macbot.setup import download_file


def prepare(name="llama"):
    manifest = json.loads((ROOT / "bootstrap/engines.json").read_text())
    engine = next(item for item in manifest["components"] if item["name"] == name)
    folder = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MacBot/Development" / name
    folder.mkdir(parents=True, exist_ok=True)
    def matches(record, location=None):
        location = location or folder
        path = location / record["path"]
        if not path.resolve().is_relative_to(location.resolve()):
            raise ValueError("The engine manifest contains an invalid path.")
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == record["sha256"]
    if all(matches(record) for record in engine["files"]):
        return
    existing = ROOT / "build/tools" / name
    if all(matches(record, existing) for record in engine["files"]):
        for record in engine["files"]:
            destination = folder / record["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(existing / record["path"], destination)
        return
    archive = folder.parent / "cache" / f"{name}-{engine['version']}.zip"
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
    prepare("diffusion")
    print("Local chat and image engines ready.")
