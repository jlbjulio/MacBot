"""Download pinned local models once; their folders travel with the portable app."""
import json
import os
import shutil
import sys
from pathlib import Path

os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from macbot.assets import MODELS, prepare_asset

directory = root / "backend/data"
arguments = sys.argv[1:]
if "--reuse-whisper-cache" in arguments:
    arguments.remove("--reuse-whisper-cache")
    revision = MODELS["whisper"][1]
    source = directory / "models/models--Systran--faster-whisper-base/snapshots" / revision
    target = directory / "models/whisper"
    if (source / "model.bin").exists():
        shutil.copytree(source, target, dirs_exist_ok=True)
        (target / "macbot-model.json").write_text(json.dumps({"repo": MODELS["whisper"][0], "revision": revision}), encoding="utf-8")
for name in arguments or MODELS:
    print(f"Preparing {name}: {MODELS[name][0]}", flush=True)
    path = prepare_asset(directory, name)
    size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    print(f"Ready: {name}; {size / 1024**2:.1f} MiB", flush=True)
