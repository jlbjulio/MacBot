import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .mcp_client import ServerConfig, add_workspace, create_server, inspect_server, public_config


class Approval(BaseModel):
    approved: bool


class TrainingRequest(BaseModel):
    examples: list[dict[str, str]] | None = None


class PersonaSetting(BaseModel):
    enabled: bool


def feature_routes(service):
    router = APIRouter(prefix="/api")
    add_workspace(service.store)

    @router.get("/persona")
    async def persona_status():
        path = service.store.directory / "models/persona-adapter/evaluation.json"
        return {"enabled": service.store.setting("persona_enabled", False),
                "status": service.store.setting("persona_training", {"state": "idle"}),
                "evaluation": json.loads(path.read_text(encoding="utf-8")) if path.exists() else None}

    @router.put("/persona")
    async def persona_setting(value: PersonaSetting):
        path = service.store.directory / "models/persona-adapter/evaluation.json"
        if value.enabled and (not path.exists() or not json.loads(path.read_text(encoding="utf-8")).get("passed")):
            raise HTTPException(409, "Train an adapter that passes its preservation checks before enabling it.")
        service.store.set_setting("persona_enabled", value.enabled)
        return {"enabled": value.enabled}

    @router.post("/persona/train")
    async def train_persona(value: TrainingRequest):
        if service.store.setting("persona_training", {}).get("state") == "running":
            raise HTTPException(409, "A training session is already running.")
        service.store.set_setting("persona_training", {"state": "running"})
        service.store.set_setting("persona_enabled", False)
        async def run():
            try:
                from .persona import train
                async with service.work_lock:
                    await asyncio.to_thread(service.retrieval.release_models)
                    await service.runtime.unload(service.store.setting("settings", {}).get("model", "qwen3.5:4b"))
                    await asyncio.to_thread(train, service.store.directory, value.examples)
                service.store.set_setting("persona_training", {"state": "completed"})
            except Exception as error:
                service.store.set_setting("persona_training", {"state": "failed", "error": str(error)[:300]})
        service.tasks["persona-training"] = asyncio.create_task(run())
        service.tasks["persona-training"].add_done_callback(lambda _: service.tasks.pop("persona-training", None))
        return {"started": True}

    @router.get("/persona/export")
    async def export_persona():
        import shutil
        path = service.store.directory / "models/persona-adapter"
        if not path.exists():
            raise HTTPException(404, "Train an adapter before exporting it.")
        target = service.store.directory / "artifacts/persona-adapter"
        await asyncio.to_thread(shutil.make_archive, str(target), "zip", str(path))
        return FileResponse(target.with_suffix(".zip"), filename="MacBot-persona-adapter.zip")

    @router.get("/capabilities")
    async def capabilities():
        from .assets import MODELS
        return {"readiness": service.setup.status()["capabilities"], "models": [{"id": key, "repo": repo, "revision": revision,
                            "ready": (service.store.directory / "models" / key / "macbot-model.json").exists()}
                           for key, (repo, revision) in MODELS.items()],
                "embedding_dimensions": 768, "portable": bool(__import__("os").environ.get("MACBOT_PORTABLE")),
                "features": ["chat", "research", "image", "audio", "spreadsheet", "presentation", "tools", "ocr", "resume"]}

    @router.get("/artifacts/{identifier}")
    async def artifact(identifier: str):
        rows = service.store.execute("SELECT * FROM artifacts WHERE id=?", (identifier,))
        if not rows:
            raise HTTPException(404, "This generated file could not be found.")
        from pathlib import Path
        path = Path(rows[0]["path"]).resolve()
        if not path.is_relative_to((service.store.directory / "artifacts").resolve()) or not path.is_file():
            raise HTTPException(404, "This generated file is unavailable.")
        return FileResponse(path, filename=rows[0]["name"])

    @router.post("/messages/{identifier}/speak")
    async def speak(identifier: str):
        rows = service.store.execute("SELECT content FROM messages WHERE id=? AND role='assistant'", (identifier,))
        if not rows:
            raise HTTPException(404, "The assistant message could not be found.")
        from .artifacts import synthesize
        path = service.store.directory / "artifacts" / f"voice-{identifier}.wav"
        if not path.exists():
            async with service.work_lock:
                await asyncio.to_thread(service.retrieval.release_models)
                await service.runtime.unload(service.store.setting("settings", {}).get("model", "qwen3.5:4b"))
                await asyncio.to_thread(synthesize, rows[0]["content"], path, service.store.directory)
        return FileResponse(path, filename="MacBot-voice.wav")

    @router.get("/mcp/servers")
    async def servers():
        return [public_config(row["id"], json.loads(row["data"])) for row in service.store.execute("SELECT * FROM mcp_servers")]

    @router.post("/mcp/servers")
    async def add_server(value: ServerConfig):
        if len(service.store.execute("SELECT id FROM mcp_servers")) >= 10:
            raise HTTPException(400, "You can save up to ten MCP connections.")
        return create_server(service.store, value)

    @router.put("/mcp/servers/{identifier}")
    async def update_server(identifier: str, value: ServerConfig):
        rows = service.store.execute("SELECT data FROM mcp_servers WHERE id=?", (identifier,))
        if not rows:
            raise HTTPException(404, "This connection could not be found.")
        previous = json.loads(rows[0]["data"])
        updated = value.model_dump()
        for field in ("env", "headers"):
            updated[field] = {key: previous.get(field, {}).get(key, "") if content == "••••••" else content
                              for key, content in updated[field].items()}
        from .secrets import seal_config
        updated = seal_config(updated)
        service.store.execute("UPDATE mcp_servers SET data=? WHERE id=?", (json.dumps(updated), identifier))
        return public_config(identifier, updated)

    @router.delete("/mcp/servers/{identifier}")
    async def delete_server(identifier: str):
        service.store.execute("DELETE FROM mcp_servers WHERE id=?", (identifier,))
        return {"removed": True}

    @router.post("/mcp/servers/{identifier}/inspect")
    async def inspect_connection(identifier: str):
        rows = service.store.execute("SELECT data FROM mcp_servers WHERE id=?", (identifier,))
        if not rows:
            raise HTTPException(404, "This connection could not be found.")
        config = json.loads(rows[0]["data"])
        try:
            return {"tools": await inspect_server(config, service.store.directory)}
        except Exception as error:
            raise HTTPException(502, f"Could not inspect this MCP connection ({type(error).__name__}). Check the command or endpoint.") from error

    def resumable(identifier, statuses):
        try:
            job = service.store.job(identifier)
        except KeyError as error:
            raise HTTPException(404, "This task could not be found.") from error
        if job["status"] not in statuses or identifier in service.tasks:
            raise HTTPException(409, "This task cannot be resumed in its current state.")
        if len([key for key in service.tasks if key != "setup"]) >= 4:
            raise HTTPException(429, "The task queue is full. Please wait.")
        if service.store.execute("SELECT id FROM jobs WHERE chat_id=? AND status IN ('queued','running')", (job["chat_id"],)):
            raise HTTPException(409, "This conversation already has an active task.")
        from .setup import require_models
        request = job["request"]
        has_files = request.get("uploads") or service.store.execute(
            "SELECT u.upload_id FROM message_uploads u JOIN messages m ON m.id=u.message_id WHERE m.chat_id=? LIMIT 1",
            (job["chat_id"],),
        )
        require_models(service.setup, request["mode"], ("embedding",) if has_files and request["mode"] == "chat" else ())
        service.store.execute("UPDATE jobs SET status='queued' WHERE id=?", (identifier,))
        return job

    @router.post("/runs/{identifier}/resume")
    async def resume(identifier: str):
        job = resumable(identifier, ("interrupted", "failed"))
        service.tasks[identifier] = asyncio.create_task(service.execute_job(identifier, job["request"], resume=True))
        return {"id": identifier}

    @router.get("/runs/{identifier}")
    async def task_detail(identifier: str):
        try:
            job = service.store.job(identifier)
            return {"id": identifier, "status": job["status"], "result": job["result"]}
        except KeyError as error:
            raise HTTPException(404, "This task could not be found.") from error

    @router.post("/runs/{identifier}/approve")
    async def approve(identifier: str, value: Approval):
        job = resumable(identifier, ("awaiting_approval",))
        service.tasks[identifier] = asyncio.create_task(service.execute_job(identifier, job["request"], resume=True, decision=value.approved))
        return {"id": identifier}

    @router.post("/uploads/{identifier}/reindex")
    async def reindex(identifier: str):
        rows = service.store.execute("SELECT * FROM uploads WHERE id=?", (identifier,))
        if not rows:
            raise HTTPException(404, "This attachment could not be found.")
        value = rows[0]
        async with service.work_lock:
            try:
                if value["kind"] == "document":
                    chunks = await asyncio.to_thread(service.retrieval.index, identifier, value["name"], value["text"])
                else:
                    chunks = await asyncio.to_thread(service.retrieval.index_media, identifier, value["name"], value["kind"], value["path"], value["text"])
                service.store.execute("INSERT OR REPLACE INTO index_state VALUES(?,?,?)", (identifier, chunks, ""))
            finally:
                await asyncio.to_thread(service.retrieval.release_models)
        return {"indexed": True, "chunks": chunks}

    return router
