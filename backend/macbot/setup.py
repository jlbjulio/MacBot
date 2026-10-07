"""First-run downloads: pinned models, resumable files and verified completion."""
import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import shutil
import threading
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from .assets import MODELS, VOICE_FILE, VOICE_PREFIX

CORE = ("voice", "whisper", "embedding", "verifier", "image")
LABELS = {"chat": "Chat and vision", "embedding": "Multimodal search", "image": "Image creation",
          "voice": "Spoken audio", "verifier": "Research evidence checks", "whisper": "Speech recognition"}
DEPENDENCIES = {"chat": ("chat",), "research": ("chat", "verifier"), "image": ("chat", "image"),
                "audio": ("chat", "voice"), "spreadsheet": ("chat",), "presentation": ("chat",),
                "document": ("chat",), "tools": ("chat",), "attachments": ("embedding",),
                "dictation": ("whisper",), "read_aloud": ("voice",)}


def require_models(controller, feature, extra=()):
    if not os.environ.get("MACBOT_PORTABLE"):
        return
    ready = {item["id"]: item["ready"] for item in controller.status()["models"]}
    missing = [LABELS[name] for name in dict.fromkeys((*DEPENDENCIES[feature], *extra)) if not ready[name]]
    if missing:
        raise HTTPException(409, "Still preparing: " + ", ".join(missing) + ". Other ready features remain available.")


def asset_ready(directory, name):
    target = directory / "models" / name
    marker = target / "macbot-model.json"
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        if value["revision"] != MODELS[name][1] or value.get("repo") != MODELS[name][0]:
            return False
        if value.get("files"):
            return all((target / item["path"]).resolve().is_relative_to(target.resolve())
                       and (target / item["path"]).is_file() and (target / item["path"]).stat().st_size == item["size"]
                       for item in value["files"])
        # Existing verified model folders from earlier MacBot builds remain usable.
        return any(target.rglob("*.safetensors")) or any(target.rglob("*.bin")) or (target / VOICE_PREFIX / VOICE_FILE).is_file()
    except (OSError, ValueError, KeyError, TypeError):
        return False


def download_file(url, target, size, digest, progress, stop, git_blob=False, candidates=(), reused=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    def verified(path):
        if not path.exists() or path.stat().st_size != size:
            return False
        check = hashlib.sha1() if git_blob else hashlib.sha256()
        if git_blob:
            check.update(f"blob {size}\0".encode())
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                if stop.is_set():
                    raise InterruptedError("Download paused.")
                check.update(block)
        return check.hexdigest() == digest
    if verified(target):
        progress(size, size)
        return
    if verified(partial):
        partial.replace(target)
        progress(size, size)
        return
    partial_size = partial.stat().st_size if partial.exists() else 0
    if shutil.disk_usage(target.parent).free + partial_size < size + 64 * 1024**2:
        raise ValueError("There is not enough free disk space to prepare this model file.")
    for candidate in candidates:
        candidate = Path(candidate)
        if candidate.resolve() == target.resolve():
            continue
        try:
            if not verified(candidate):
                continue
            # Copy into our own folder; never alter or depend on another application's cache.
            with candidate.open("rb") as source, partial.open("wb") as output:
                copied = 0
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    if stop.is_set():
                        raise InterruptedError("Download paused.")
                    output.write(block)
                    copied += len(block)
                    progress(copied, size)
            if not verified(partial):
                partial.unlink(missing_ok=True)
                continue
            partial.replace(target)
            if reused:
                reused(size)
            return
        except OSError:
            continue
    if partial.exists() and partial.stat().st_size > size:
        partial.unlink()
    offset = partial.stat().st_size if partial.exists() else 0
    with httpx.Client(timeout=httpx.Timeout(45, connect=15), follow_redirects=True, trust_env=False) as client:
        with client.stream("GET", url, headers={"Range": f"bytes={offset}-"} if offset else {}) as response:
            response.raise_for_status()
            if offset and response.status_code != 206:
                offset = 0
            if response.status_code == 206 and not response.headers.get("content-range", "").startswith(f"bytes {offset}-"):
                raise ValueError("The download server returned an invalid resume range.")
            with partial.open("ab" if offset else "wb") as output:
                for block in response.iter_bytes(1024 * 1024):
                    if stop.is_set():
                        raise InterruptedError("Download paused.")
                    offset += len(block)
                    if offset > size:
                        raise ValueError("The downloaded file exceeded its expected size.")
                    output.write(block)
                    progress(offset, size)
    if not verified(partial):
        partial.unlink(missing_ok=True)
        raise ValueError("A model file failed its integrity check. Retry the download.")
    partial.replace(target)


def download_asset(directory, name, progress=lambda *args: None, stop=None, reused=None):
    from huggingface_hub import HfApi, hf_hub_url, try_to_load_from_cache
    stop = stop or threading.Event()
    if asset_ready(directory, name):
        return directory / "models" / name
    repo, revision = MODELS[name]
    information = HfApi().model_info(repo, revision=revision, files_metadata=True, token=False)
    patterns = ["*.json", "*.safetensors", "*.model", "*.jinja", "*.txt", "*.md", "LICENSE*"]
    if name in ("image", "whisper"):
        patterns += ["*.bin"]
    if name == "voice":
        patterns = [VOICE_PREFIX + VOICE_FILE, VOICE_PREFIX + VOICE_FILE + ".json", VOICE_PREFIX + "MODEL_CARD"]
    files = [item for item in information.siblings or [] if any(fnmatch.fnmatch(item.rfilename, pattern) for pattern in patterns)
             and not any(tag in item.rfilename.lower() for tag in ("fp16", "flax", "onnx")) or
             name == "voice" and item.rfilename in patterns]
    if not files:
        raise ValueError("The pinned model revision has no downloadable files.")
    target = directory / "models" / name
    total, finished, recorded = sum(item.size or 0 for item in files), 0, []
    if shutil.disk_usage(directory).free < total + 512 * 1024**2:
        raise ValueError("There is not enough free disk space for this model. Free some space and retry.")
    for item in files:
        path = (target / item.rfilename).resolve()
        if not path.is_relative_to(target.resolve()):
            raise ValueError("The model manifest contains an invalid file path.")
        digest = item.lfs.sha256 if item.lfs else item.blob_id
        if not item.size or not digest:
            if item.size == 0:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"")
                continue
            raise ValueError("The model host did not provide a file size and checksum.")
        caches = {Path.home() / ".cache/huggingface/hub", directory / "cache/huggingface/hub"}
        for variable in ("MACBOT_EXISTING_HF_CACHE", "HF_HUB_CACHE"):
            if os.environ.get(variable):
                caches.add(Path(os.environ[variable]))
        candidates = []
        for cache in caches:
            cached = try_to_load_from_cache(repo, item.rfilename, revision=revision, cache_dir=str(cache))
            if isinstance(cached, str):
                candidates.append(cached)
        download_file(hf_hub_url(repo, item.rfilename, revision=revision), path, item.size, digest,
                      lambda done, size: progress(name, finished + done, total, item.rfilename), stop,
                      git_blob=not item.lfs, candidates=candidates, reused=reused)
        finished += item.size
        recorded.append({"path": item.rfilename, "size": item.size, "digest": digest})
    temporary = target / "macbot-model.json.partial"
    temporary.write_text(json.dumps({"repo": repo, "revision": revision, "files": recorded}), encoding="utf-8")
    temporary.replace(target / "macbot-model.json")
    return target


class Setup:
    def __init__(self, directory):
        self.directory = directory
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.state = {"status": "waiting", "model": "", "completed": 0, "total": 0, "file": "", "error": "",
                      "reused_bytes": 0, "failures": {}}
        self.task: asyncio.Task[None] | None = None
        self.manifest = json.loads((Path(__file__).resolve().parents[1] / "ollama-model.json").read_text()) if (Path(__file__).resolve().parents[1] / "ollama-model.json").exists() else None

    def chat_ready(self):
        root = self.directory / "models/ollama"
        if not self.manifest:
            return False
        manifest = root / "manifests/registry.ollama.ai/library/qwen3.5/4b"
        try:
            if json.loads(manifest.read_text()) != self.manifest:
                return False
        except (OSError, ValueError):
            return False
        return all(
            (root / "blobs" / item["digest"].replace(":", "-")).is_file() and
            (root / "blobs" / item["digest"].replace(":", "-")).stat().st_size == item["size"]
            for item in [self.manifest["config"], *self.manifest["layers"]])

    def status(self):
        ready = {"chat": self.chat_ready(), **{name: asset_ready(self.directory, name) for name in CORE}}
        with self.lock:
            state = {**self.state, "failures": dict(self.state["failures"])}
        if all(ready.values()):
            state["status"] = "ready"
            state["error"] = ""
            state["failures"] = {}
        return {**state, "models": [{"id": name, "name": LABELS[name], "ready": value} for name, value in ready.items()],
                "capabilities": {feature: all(ready[name] for name in names) for feature, names in DEPENDENCIES.items()},
                "directory": str(self.directory), "download_sources": ["Hugging Face", "Ollama model registry"]}

    def reused(self, size):
        with self.lock:
            self.state["reused_bytes"] += size

    def update(self, name, completed, total, filename):
        with self.lock:
            self.state.update(model=LABELS.get(name, name), completed=completed, total=total, file=filename)

    def download_chat(self):
        if self.chat_ready():
            return
        if not self.manifest:
            raise ValueError("The portable chat manifest is missing. Download a complete MacBot release.")
        root = self.directory / "models/ollama"
        items = [self.manifest["config"], *self.manifest["layers"]]
        total, finished = sum(item["size"] for item in items), 0
        caches = [Path.home() / ".ollama/models"]
        for variable in ("MACBOT_EXISTING_OLLAMA_MODELS", "OLLAMA_MODELS"):
            if os.environ.get(variable):
                caches.append(Path(os.environ[variable]))
        for item in items:
            digest = item["digest"]
            download_file("https://registry.ollama.ai/v2/library/qwen3.5/blobs/" + digest,
                          root / "blobs" / digest.replace(":", "-"), item["size"], digest.split(":", 1)[1],
                          lambda done, size: self.update("chat", finished + done, total, "Chat model weights"), self.stop,
                          candidates=[cache / "blobs" / digest.replace(":", "-") for cache in caches], reused=self.reused)
            finished += item["size"]
        manifest = root / "manifests/registry.ollama.ai/library/qwen3.5/4b"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest.with_suffix(".partial")
        temporary.write_text(json.dumps(self.manifest), encoding="utf-8")
        temporary.replace(manifest)

    async def run(self, service):
        with self.lock:
            self.state.update(status="downloading", error="", failures={})
        try:
            for name in ("chat", *CORE):
                if self.stop.is_set():
                    raise InterruptedError("Download paused.")
                self.update(name, 0, 0, "")
                try:
                    if name == "chat":
                        await asyncio.to_thread(self.download_chat)
                    else:
                        await asyncio.to_thread(download_asset, self.directory, name, self.update, self.stop, self.reused)
                except InterruptedError:
                    raise
                except Exception as error:
                    logging.getLogger("macbot").exception("Model preparation failed: %s", name)
                    with self.lock:
                        self.state["failures"][name] = f"Could not prepare {LABELS[name]} ({type(error).__name__})."
            with self.lock:
                failed = bool(self.state["failures"])
                self.state.update(status="failed" if failed else "ready", model="", file="", completed=0, total=0,
                                  error="Some downloads need a retry. Check your connection and free disk space. Ready features still work." if failed else "")
        except InterruptedError:
            with self.lock:
                self.state.update(status="paused")
        except Exception as error:
            logging.getLogger("macbot").exception("First-run model download failed")
            with self.lock:
                self.state.update(status="failed", error=f"Download could not finish ({type(error).__name__}). Check your internet connection and free disk space, then retry.")


def setup_routes(service):
    controller = getattr(service, "setup", None) or Setup(service.store.directory)
    service.setup = controller
    router = APIRouter(prefix="/api")

    @router.get("/setup")
    async def status():
        return controller.status()

    @router.post("/setup")
    async def start():
        if controller.task and not controller.task.done():
            return controller.status()
        if controller.status()["status"] == "ready":
            return controller.status()
        controller.stop.clear()
        controller.task = asyncio.create_task(controller.run(service))
        service.tasks["setup"] = controller.task
        controller.task.add_done_callback(lambda _: service.tasks.pop("setup", None))
        return {"started": True}

    @router.post("/setup/pause")
    async def pause():
        controller.stop.set()
        return {"paused": True}
    return router
