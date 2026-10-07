import asyncio
import json

import httpx
import pytest

from macbot import api as module
from macbot.documents import export_document, extract
from macbot.graph import build_graph
from macbot.runtime import local_model
from macbot.store import Store


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "work_lock", asyncio.Lock())
    yield store
    store.db.close()


def test_cloud_model_is_rejected():
    with pytest.raises(ValueError):
        local_model("qwen:cloud")


def test_history_and_failed_jobs_survive_restart(tmp_path):
    store = Store(tmp_path)
    chat = store.create_chat()
    store.message(chat["id"], "user", "Hola")
    store.execute(
        "INSERT INTO jobs(id,chat_id,kind,status,request,created) VALUES(?,?,?,?,?,?)",
        ("job", chat["id"], "chat", "running", "{}", "2026"),
    )
    store.db.close()
    reopened = Store(tmp_path)
    assert reopened.messages(chat["id"])[0]["content"] == "Hola"
    assert reopened.job("job")["status"] == "interrupted"
    reopened.db.close()


async def test_api_requires_token_and_keeps_conversation(isolated):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=module.app), base_url="http://test"
    ) as client:
        denied = await client.get("/api/chats")
        assert denied.status_code == 401
        assert denied.headers["cache-control"] == "no-store"
        client.headers["Authorization"] = f"Bearer {module.TOKEN}"
        chat = (await client.post("/api/chats")).json()
        history = await client.get("/api/chats")
        assert history.json()[0]["id"] == chat["id"]
        assert history.headers["cache-control"] == "no-store"
        invalid = await client.post(
            "/api/runs", json={"chat_id": chat["id"], "prompt": "Hola", "model": "x:cloud"}
        )
        assert invalid.status_code == 400


async def test_api_stream_and_cancel_preserve_job_state(isolated, monkeypatch):
    started = asyncio.Event()

    class Runtime:
        async def stream(self, model, messages, json_mode=False):
            yield "Respuesta"
            if messages[-1]["content"] == "Espera":
                started.set()
                await asyncio.sleep(60)

    monkeypatch.setattr(module, "runtime", Runtime())
    headers = {"Authorization": f"Bearer {module.TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=module.app), base_url="http://test", headers=headers
    ) as client:
        chat = (await client.post("/api/chats")).json()
        run = (await client.post("/api/runs", json={"chat_id": chat["id"], "prompt": "Hola"})).json()
        events = await client.get(f"/api/runs/{run['id']}/events")
        assert '"type": "token"' in events.text
        assert '"type": "done"' in events.text
        assert isolated.messages(chat["id"])[-1]["content"] == "Respuesta"
        run = (await client.post("/api/runs", json={"chat_id": chat["id"], "prompt": "Espera"})).json()
        task = module.tasks[run["id"]]
        await asyncio.wait_for(started.wait(), 2)
        assert (await client.post(f"/api/runs/{run['id']}/cancel")).json()["cancelled"]
        await task
        assert isolated.job(run["id"])["status"] == "cancelled"
        assert isolated.messages(chat["id"])[-1]["role"] == "user"


async def test_graph_streams_real_runtime_output_and_reads_attachment(isolated):
    chat = isolated.create_chat()
    isolated.message(chat["id"], "user", "¿Qué dice el archivo?")
    isolated.execute(
        "INSERT INTO uploads VALUES(?,?,?,?,?)", ("doc", "nota.txt", "document", "Contenido especial", "")
    )
    captured = []

    class Runtime:
        async def stream(self, model, messages, json_mode=False):
            assert "Contenido especial" in messages[-1]["content"]
            yield "Contenido "
            yield "especial"

    result = await build_graph(Runtime(), isolated, captured.append).ainvoke(
        {"chat_id": chat["id"], "mode": "chat", "model": "local", "uploads": ["doc"], "prompt": "pregunta"}
    )
    assert result["answer"] == "Contenido especial"
    assert [e["text"] for e in captured if e["type"] == "token"] == ["Contenido ", "especial"]


async def test_research_does_not_publish_uncited_draft(isolated, monkeypatch):
    from macbot import graph as graph_module

    async def search(query):
        return [{"href": "https://example.org", "title": "Official source"}]

    async def read(url):
        return "Evidence from the source"

    monkeypatch.setattr(graph_module, "search", search)
    monkeypatch.setattr(graph_module, "read_page", read)

    class Runtime:
        async def complete(self, model, prompt, json_mode=False, max_tokens=1800):
            return json.dumps({"queries": ["query"]}) if json_mode else "Uncited assertion"

    captured = []
    with pytest.raises(ValueError):
        await build_graph(Runtime(), isolated, captured.append).ainvoke(
            {"chat_id": "c", "mode": "research", "model": "local", "prompt": "question"}
        )
    assert not any(e["type"] == "token" for e in captured)


async def test_research_includes_each_query_and_only_reads_six_sources(isolated, monkeypatch):
    from macbot import graph as graph_module

    async def search(query):
        return [{"href": f"https://example.org/{query}/{i}", "title": f"{query}-{i}"} for i in range(5)]

    async def read(url):
        return "Source text"

    monkeypatch.setattr(graph_module, "search", search)
    monkeypatch.setattr(graph_module, "read_page", read)

    class Runtime:
        async def complete(self, model, prompt, json_mode=False, max_tokens=1800):
            return json.dumps({"queries": ["a", "b", "c"]}) if "searches" in prompt else json.dumps({"claims": []})

        async def unload(self, model):
            pass

    result = await build_graph(Runtime(), isolated, lambda _: None).ainvoke(
        {"chat_id": "c", "mode": "research", "model": "local", "prompt": "question"}
    )
    assert len(result["sources"]) == 6
    assert all(any(f"/{q}/" in s["url"] for s in result["sources"]) for q in ("a", "b", "c"))
    assert all(s["status"] == "read" for s in result["sources"])


@pytest.mark.parametrize("fmt", ["md", "txt", "pdf", "docx"])
def test_downloadable_artifacts_open_with_content(tmp_path, fmt):
    path = tmp_path / f"answer.{fmt}"
    export_document("# Resultado\nContenido comprobable", path, fmt)
    if fmt in ("pdf", "docx"):
        assert "Contenido comprobable" in extract(path.name, path.read_bytes())[1]
    else:
        assert "Contenido comprobable" in path.read_text(encoding="utf-8")
