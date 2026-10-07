import asyncio
import hashlib
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from macbot import setup


def test_ready_features_do_not_depend_on_unrelated_models(tmp_path, monkeypatch):
    controller = setup.Setup(tmp_path)
    monkeypatch.setattr(controller, "chat_ready", lambda: True)
    monkeypatch.setattr(setup, "asset_ready", lambda directory, name: name == "voice")
    monkeypatch.setenv("MACBOT_PORTABLE", "1")
    status = controller.status()
    assert status["capabilities"]["chat"] and status["capabilities"]["document"]
    assert status["capabilities"]["audio"] and status["capabilities"]["read_aloud"]
    assert not status["capabilities"]["image"] and not status["capabilities"]["dictation"]
    setup.require_models(controller, "chat")
    with pytest.raises(HTTPException) as error:
        setup.require_models(controller, "image")
    assert error.value.status_code == 409 and "Image creation" in error.value.detail
    with pytest.raises(HTTPException):
        setup.require_models(controller, "chat", ("embedding",))


async def test_downloads_unlock_individually_without_holding_inference_lock(tmp_path, monkeypatch):
    controller = setup.Setup(tmp_path)
    available = set()
    monkeypatch.setattr(controller, "chat_ready", lambda: "chat" in available)
    monkeypatch.setattr(setup, "asset_ready", lambda directory, name: name in available)
    monkeypatch.setattr(controller, "download_chat", lambda: available.add("chat"))
    seen = []
    def download(directory, name, *args):
        seen.append(controller.status()["capabilities"]["chat"])
        if name == "voice":
            raise OSError("Disconnected voice source")
        available.add(name)
    monkeypatch.setattr(setup, "download_asset", download)
    lock = asyncio.Lock()
    async with lock:
        await asyncio.wait_for(controller.run(SimpleNamespace(work_lock=lock)), timeout=2)
    assert all(seen)
    status = controller.status()
    assert status["status"] == "failed" and list(status["failures"]) == ["voice"]
    assert status["capabilities"]["image"] and status["capabilities"]["dictation"]
    assert not status["capabilities"]["audio"]


def test_existing_file_is_verified_and_copied_without_network(tmp_path, monkeypatch):
    source = tmp_path / "existing.bin"
    source.write_bytes(b"already downloaded")
    target = tmp_path / "portable/model.bin"
    reused = []
    monkeypatch.setattr(setup.httpx, "Client", lambda **kwargs: pytest.fail("No model transfer should occur"))
    setup.download_file("https://models.example/weights", target, source.stat().st_size,
                        hashlib.sha256(source.read_bytes()).hexdigest(), lambda *args: None,
                        threading.Event(), candidates=[source], reused=reused.append)
    assert target.read_bytes() == source.read_bytes() and reused == [source.stat().st_size]
    source.write_bytes(b"changed outside cache")
    assert target.read_bytes() == b"already downloaded"


def test_corrupt_existing_cache_falls_back_to_verified_download(tmp_path, monkeypatch):
    source = tmp_path / "existing.bin"
    source.write_bytes(b"wrong same length")
    data = b"valid same length"
    original = httpx.Client
    calls = []
    def respond(request):
        calls.append(request.url)
        return httpx.Response(200, content=data)
    monkeypatch.setattr(setup.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    target = tmp_path / "portable/model.bin"
    setup.download_file("https://models.example/weights", target, len(data), hashlib.sha256(data).hexdigest(),
                        lambda *args: None, threading.Event(), candidates=[source])
    assert len(calls) == 1 and target.read_bytes() == data and source.read_bytes() == b"wrong same length"


def test_hugging_face_cache_reuse_uses_exact_pinned_revision(tmp_path, monkeypatch):
    import huggingface_hub as hub
    source = tmp_path / "cache.bin"
    data = b"verified existing voice"
    source.write_bytes(data)
    item = SimpleNamespace(rfilename=setup.VOICE_PREFIX + setup.VOICE_FILE, size=len(data),
                           lfs=SimpleNamespace(sha256=hashlib.sha256(data).hexdigest()), blob_id=None)
    monkeypatch.setattr(hub, "HfApi", lambda: SimpleNamespace(model_info=lambda *args, **kwargs: SimpleNamespace(siblings=[item])))
    revisions = []
    def cached(repo, filename, *, revision, cache_dir):
        revisions.append(revision)
        return str(source)
    monkeypatch.setattr(hub, "try_to_load_from_cache", cached)
    monkeypatch.setattr(setup.httpx, "Client", lambda **kwargs: pytest.fail("Cached weights must not download"))
    setup.download_asset(tmp_path, "voice")
    assert revisions and set(revisions) == {setup.MODELS["voice"][1]}
    assert setup.asset_ready(tmp_path, "voice")


async def test_portable_metadata_remains_available_during_preparation(monkeypatch):
    from macbot import api
    monkeypatch.setenv("MACBOT_PORTABLE", "1")
    monkeypatch.setattr(api, "setup", SimpleNamespace(status=lambda: {
        "models": [{"id": key, "ready": key == "chat"} for key in ("chat", *setup.CORE)],
    }))
    headers = {"Authorization": f"Bearer {api.TOKEN}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
        assert (await client.get("/api/settings", headers=headers)).status_code == 200
        for endpoint in ("/api/transcribe", "/api/uploads", "/api/messages/not-found/speak"):
            response = await client.post(endpoint, headers=headers)
            assert response.status_code == 409 and response.headers["cache-control"] == "no-store"
        response = await client.post("/api/runs", headers=headers, json={"chat_id": "missing", "prompt": "hello", "mode": "chat"})
        assert response.status_code == 404  # Model readiness passed; the conversation is missing.
        response = await client.post("/api/runs", headers=headers, json={"chat_id": "missing", "prompt": "Generate an image of a moon"})
        assert response.status_code == 409 and "Image creation" in response.json()["detail"]
        monkeypatch.setattr(api, "setup", SimpleNamespace(status=lambda: {
            "models": [{"id": key, "ready": key in ("chat", "embedding")} for key in ("chat", *setup.CORE)],
        }))
        for filename in ("clip.wav", "clip.mp4"):
            response = await client.post("/api/uploads", headers=headers, files={"file": (filename, b"not media")})
            assert response.status_code == 409 and "Speech recognition" in response.json()["detail"]


def test_readiness_rejects_wrong_repository_and_paths_outside_model(tmp_path):
    target = tmp_path / "models/voice"
    target.mkdir(parents=True)
    marker = target / "macbot-model.json"
    import json
    marker.write_text(json.dumps({"repo": "different/model", "revision": setup.MODELS["voice"][1]}))
    assert not setup.asset_ready(tmp_path, "voice")
    outside = tmp_path / "models/outside.bin"
    outside.write_bytes(b"outside")
    marker.write_text(json.dumps({"repo": setup.MODELS["voice"][0], "revision": setup.MODELS["voice"][1],
                                  "files": [{"path": "../outside.bin", "size": outside.stat().st_size}]}))
    assert not setup.asset_ready(tmp_path, "voice")
