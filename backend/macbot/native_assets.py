import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import threading


def creation_manifest(name):
    return json.loads((Path(__file__).resolve().parents[1] / "creation-models.json").read_text(encoding="utf-8"))[name]


def image_profile():
    import psutil
    if psutil.virtual_memory().total >= 24 * 1024**3:
        try:
            result = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if result.returncode == 0 and any(int(line.strip()) >= 8192 for line in result.stdout.splitlines()):
                return "balanced"
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return "compact"


def asset_manifest(name, profile=None):
    spec = creation_manifest(name)
    profile = profile or (image_profile() if name == "image" else "default")
    records = list(spec["files"])
    if "profiles" in spec:
        if profile not in spec["profiles"]:
            raise ValueError("Unsupported image model profile.")
        records += spec["profiles"][profile]
    return {"repo": spec["repo"], "revision": spec["revision"], "profile": profile, "files": records}


def native_ready(directory, name):
    root = directory / "models" / name
    try:
        marker = json.loads((root / "macbot-model.json").read_text(encoding="utf-8"))
        expected = asset_manifest(name, marker["profile"])
        return marker == expected and all((root / f["path"]).resolve().is_relative_to(root.resolve())
            and (root / f["path"]).is_file() and (root / f["path"]).stat().st_size == f["size"] for f in expected["files"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def download_native(directory, name, progress, stop=None, reused=None):
    from huggingface_hub import hf_hub_url, try_to_load_from_cache
    from .setup import download_file
    stop = stop or threading.Event()
    root = directory / "models" / name
    if native_ready(directory, name):
        return root
    manifest = asset_manifest(name)
    records = manifest["files"]
    total, finished = sum(f["size"] for f in records), 0
    remaining = sum(max(0, f["size"] - ((root / f["path"]).stat().st_size if (root / f["path"]).is_file() else 0)) for f in records)
    if shutil.disk_usage(directory).free < remaining + 512 * 1024**2:
        raise ValueError("There is not enough free disk space for this model. Free some space and retry.")
    caches = {Path.home() / ".cache/huggingface/hub", directory / "cache/huggingface/hub"}
    caches.update(Path(os.environ[v]) for v in ("MACBOT_EXISTING_HF_CACHE", "HF_HUB_CACHE") if os.environ.get(v))
    for f in records:
        target = (root / f["path"]).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ValueError("The model manifest contains an invalid path.")
        candidates = [try_to_load_from_cache(f["repo"], f["source"], revision=f["revision"], cache_dir=str(c)) for c in caches]
        download_file(hf_hub_url(f["repo"], f["source"], revision=f["revision"]), target, f["size"], f["digest"],
            lambda done, size: progress(name, finished + done, total, f["path"]), stop,
            git_blob=f["git_blob"], candidates=[c for c in candidates if isinstance(c, str)], reused=reused)
        finished += f["size"]
    temporary = root / "macbot-model.json.partial"
    temporary.write_text(json.dumps(manifest), encoding="utf-8")
    temporary.replace(root / "macbot-model.json")
    return root


def fingerprint(name):
    digest = hashlib.sha256(json.dumps(creation_manifest(name), sort_keys=True).encode())
    for module in ("documents.py", "media.py", "ocr.py", "assets.py", "retrieval.py"):
        digest.update((Path(__file__).parent / module).read_bytes())
    return digest.hexdigest()[:16]
