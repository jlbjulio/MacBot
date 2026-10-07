import asyncio
import hashlib
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from macbot import setup
from macbot.privacy import privacy_routes, remove_owned
from macbot.store import Store


async def test_cold_start_gate_returns_readable_native_error(monkeypatch):
    from macbot import api
    monkeypatch.setenv("MACBOT_PORTABLE", "1")
    monkeypatch.setattr(api, "setup", SimpleNamespace(status=lambda: {"status": "waiting", "models": [{"id": "whisper", "ready": False}]}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
        response = await client.post("/api/transcribe", json={}, headers={
            "Authorization": f"Bearer {api.TOKEN}", "Origin": "http://tauri.localhost"})
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_verified_download_resumes_and_rejects_corruption(tmp_path, monkeypatch):
    data = b"verified model bytes"
    target = tmp_path / "model.bin"
    target.with_suffix(".bin.partial").write_bytes(data[:5])
    calls = []

    def respond(request):
        calls.append(request.headers.get("range"))
        return httpx.Response(206, content=data[5:], headers={"Content-Range": f"bytes 5-{len(data)-1}/{len(data)}"})

    original = httpx.Client
    monkeypatch.setattr(setup.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    setup.download_file("https://models.example/model", target, len(data), hashlib.sha256(data).hexdigest(), lambda *args: None, threading.Event())
    assert target.read_bytes() == data and calls == ["bytes=5-"]
    setup.download_file("https://models.example/model", target, len(data), hashlib.sha256(data).hexdigest(), lambda *args: None, threading.Event())
    assert len(calls) == 1
    target.unlink()
    target.with_suffix(".bin.partial").write_bytes(data[:5])
    with pytest.raises(ValueError):
        setup.download_file("https://models.example/model", target, len(data), "0" * 64, lambda *args: None, threading.Event())
    assert not target.exists()


def test_personal_deletion_cannot_leave_workspace(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError):
        remove_owned(workspace, outside)
    assert outside.read_text() == "keep"


async def test_workspace_erasure_requires_confirmation_and_keeps_public_models(tmp_path):
    store = Store(tmp_path)
    store.message(store.create_chat()["id"], "user", "Private content")
    (tmp_path / "models/embedding").mkdir(parents=True)
    (tmp_path / "models/embedding/model.bin").write_bytes(b"public")
    (tmp_path / "workspace").mkdir(exist_ok=True)
    (tmp_path / "workspace/private.txt").write_text("private")
    service = SimpleNamespace(store=store, tasks={}, work_lock=asyncio.Lock(),
                              retrieval=SimpleNamespace(release_models=lambda: None, close=lambda: None))
    app = FastAPI()
    app.include_router(privacy_routes(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/privacy/erase", json={"confirmation": "no"})).status_code == 400
        response = await client.post("/api/privacy/erase", json={"confirmation": "ERASE MY WORKSPACE"})
        assert response.status_code == 200, response.text
    assert store.chats() == []
    assert not (tmp_path / "workspace/private.txt").exists()
    assert (tmp_path / "models/embedding/model.bin").read_bytes() == b"public"
    store.db.close()
