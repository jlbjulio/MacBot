import asyncio
import importlib.util
import json
import logging
import os
import secrets
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from .documents import export_document, extract
from .features import feature_routes
from .graph import build_graph
from .runtime import MODEL_NAME, REASONING, LlamaRuntime, local_model
from .retrieval import RetrievalIndex
from .store import Store, data_directory, now
from .setup import Setup, setup_routes, require_models
from .privacy import privacy_routes

logger = logging.getLogger("macbot")
store = Store(data_directory())
runtime = LlamaRuntime(store.directory)
TOKEN = os.environ.get("MACBOT_TOKEN") or secrets.token_hex(32)
tasks: dict[str, asyncio.Task] = {}
work_lock = asyncio.Lock()
voice_model = None
setup = Setup(store.directory)
retrieval = RetrievalIndex(store.directory, engine="gemma2")


@asynccontextmanager
async def lifespan(app):
    if not os.environ.get("MACBOT_TOKEN"):
        logger.warning("MACBOT_TOKEN is missing. Start through the desktop app or the development launcher.")
    yield
    if hasattr(sys.modules[__name__], "setup"):
        setup.stop.set()
    for task in list(tasks.values()):
        task.cancel()
    await asyncio.gather(*tasks.values(), return_exceptions=True)
    retrieval.close()
    await runtime.unload()


app = FastAPI(title="MacBot", lifespan=lifespan, docs_url=None, redoc_url=None)



@app.middleware("http")
async def authenticate(request: Request, call_next):
    limit = 21 * 1024**2 if request.url.path in ("/api/uploads", "/api/transcribe") else 2 * 1024**2
    length = request.headers.get("content-length", "0")
    if not length.isdigit() or int(length) > limit:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "This request is too large."}, status_code=413)
    if request.method != "OPTIONS" and not secrets.compare_digest(
        request.headers.get("authorization", ""), f"Bearer {TOKEN}"
    ):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    if request.method == "POST":
        feature = ("dictation" if request.url.path == "/api/transcribe" else
                   "attachments" if request.url.path == "/api/uploads" or request.url.path.endswith("/reindex") else
                   "read_aloud" if request.url.path.startswith("/api/messages/") and request.url.path.endswith("/speak") else None)
        if feature:
            try:
                require_models(setup, feature)
            except HTTPException as error:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": error.detail}, status_code=error.status_code)
    return await call_next(request)


@app.middleware("http")
async def prevent_history_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


RunMode = Literal["chat", "research", "image", "audio", "spreadsheet", "presentation", "document", "tools"]


class RunRequest(BaseModel):
    chat_id: str
    prompt: str = Field(min_length=1, max_length=12000)
    mode: RunMode = "chat"
    model: str = Field(default=MODEL_NAME, min_length=1, max_length=150)
    reasoning: Literal["low", "medium", "high"] = "medium"
    uploads: list[str] = Field(default_factory=list, max_length=5)


class Settings(BaseModel):
    model: str = MODEL_NAME
    reasoning: Literal["low", "medium", "high"] = "medium"
    whisper_model: Literal["tiny", "base", "small"] = "base"
    language: Literal["es", "en", "auto"] = "en"


class ModelDownload(BaseModel):
    model: Literal["macbot-4b"]


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.3.0"}


@app.get("/api/models")
async def models():
    return await runtime.models()


@app.post("/api/models/download")
async def start_model_download(value: ModelDownload):
    if setup.task and not setup.task.done():
        raise HTTPException(409, "Model preparation is already running.")
    setup.stop.clear()
    setup.task = asyncio.create_task(setup.run(sys.modules[__name__]))
    tasks["setup"] = setup.task
    setup.task.add_done_callback(lambda _: tasks.pop("setup", None))
    return {"started": True}


@app.get("/api/diagnostics")
async def diagnostics():
    import psutil

    return {
        "data_directory": str(store.directory),
        "backend_rss_mb": round(psutil.Process().memory_info().rss / 1e6, 1),
        "queued_jobs": len(tasks),
        "indexed_documents": store.execute("SELECT COUNT(*) AS n FROM index_state WHERE chunks>0")[0]["n"],
    }


@app.get("/api/settings")
async def settings():
    value = {**Settings().model_dump(), **store.setting("settings", {})}
    value["model"] = MODEL_NAME
    value["reasoning"] = value.get("reasoning") if value.get("reasoning") in ("low", "medium", "high") else "medium"
    return {**value, "voice_ready": importlib.util.find_spec("faster_whisper") is not None}


@app.put("/api/settings")
async def save_settings(value: Settings):
    try:
        value.model = local_model(value.model)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    store.set_setting("settings", value.model_dump())
    return value


@app.get("/api/chats")
async def chats():
    return store.chats()


@app.post("/api/chats")
async def create_chat():
    return store.create_chat()


@app.get("/api/chats/{chat_id}")
async def conversation(chat_id: str):
    if not store.execute("SELECT id FROM chats WHERE id=?", (chat_id,)):
        raise HTTPException(404, "Conversation not found.")
    return {
        "messages": store.messages(chat_id),
        "jobs": store.execute(
            "SELECT id,status,kind FROM jobs WHERE chat_id=? ORDER BY created DESC LIMIT 1", (chat_id,)
        ),
    }


async def execute_job(job_id, value, resume=False, decision=None):
    def emit(event):
        store.event(job_id, event)

    reasoning_token = REASONING.set(value.get("reasoning", "medium"))
    try:
        emit({"type": "stage", "label": "Queued", "agent": "supervisor"})
        async with work_lock:
            store.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.types import Command
            async with AsyncSqliteSaver.from_conn_string(str(store.directory / "checkpoints.sqlite3")) as checkpointer:
                graph = build_graph(runtime, store, emit, retrieval=retrieval, checkpointer=checkpointer)
                config: RunnableConfig = {"recursion_limit": 16, "configurable": {"thread_id": job_id}}
                value["job_id"] = job_id
                snapshot = await graph.aget_state(config) if resume else None
                inputs = Command(resume=decision) if decision is not None else None if snapshot and snapshot.values else value
                if resume:
                    emit({"type": "reset"})
                result = await graph.ainvoke(inputs, config)
                if result.get("__interrupt__"):
                    pending = result["__interrupt__"][0].value
                    store.execute("UPDATE jobs SET status='awaiting_approval',result=? WHERE id=?", (json.dumps(pending), job_id))
                    emit({"type": "approval", **pending})
                    return
            message_id = store.finish_job(job_id, value["chat_id"], result)
            emit({"type": "done", "message_id": message_id})
    except asyncio.CancelledError:
        store.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
        emit({"type": "cancelled"})
    except Exception as error:
        logger.exception("Job %s failed", job_id)
        detail = str(error)[:500]
        store.execute(
            "UPDATE jobs SET status='failed',result=? WHERE id=?", (json.dumps({"error": detail}), job_id)
        )
        emit({"type": "error", "message": detail})
    finally:
        REASONING.reset(reasoning_token)
        tasks.pop(job_id, None)


@app.post("/api/runs")
async def run(value: RunRequest):
    if value.mode == "chat":
        import re
        direct = re.match(r"^\s*(?:please\s+)?(?:(?:can|could) you\s+)?(?:create|generate|make|design|prepare|build|crea|genera|diseña|prepara)\s+(.{0,100})", value.prompt, re.I)
        if direct:
            kinds: dict[RunMode, str] = {"image": r"\b(?:image|picture|illustration|photo|imagen|ilustración)\b", "audio": r"\b(?:audio|voice recording|spoken script)\b",
                     "spreadsheet": r"\b(?:spreadsheet|workbook|excel|hoja de cálculo)\b", "presentation": r"\b(?:presentation|slides|slide deck|presentación|diapositivas)\b",
                     "document": r"\b(?:document|report|pdf|docx|word file|documento|informe)\b"}
            matches: list[tuple[int, RunMode]] = [(match.start(), mode) for mode, pattern in kinds.items() if (match := re.search(pattern, direct[1], re.I))]
            if matches:
                value.mode = min(matches)[1]
    previous_uploads = store.execute(
        "SELECT u.upload_id FROM message_uploads u JOIN messages m ON m.id=u.message_id WHERE m.chat_id=? LIMIT 1",
        (value.chat_id,),
    ) if value.mode == "chat" else []
    require_models(setup, value.mode, ("embedding",) if value.uploads or previous_uploads else ())
    if len([key for key in tasks if key != "setup"]) >= 4:
        raise HTTPException(429, "The queue is full. Please wait for a task to finish.")
    if not store.execute("SELECT id FROM chats WHERE id=?", (value.chat_id,)):
        raise HTTPException(404, "Conversation not found.")
    if store.execute(
        "SELECT id FROM jobs WHERE chat_id=? AND status IN ('queued','running')", (value.chat_id,)
    ):
        raise HTTPException(409, "This conversation already has an active task.")
    try:
        value.model = local_model(value.model)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if value.mode == "research" and value.uploads:
        raise HTTPException(
            400, "Deep Research follows web sources. Use Chat to explore your attachments."
        )
    for upload_id in value.uploads:
        if not store.execute("SELECT id FROM uploads WHERE id=?", (upload_id,)):
            raise HTTPException(404, "Attachment not found.")
    message_id = store.message(value.chat_id, "user", value.prompt)
    for upload_id in value.uploads:
        store.execute("INSERT OR IGNORE INTO message_uploads VALUES(?,?)", (message_id, upload_id))
    jid = uuid4().hex
    store.execute(
        "INSERT INTO jobs(id,chat_id,kind,status,request,created) VALUES(?,?,?,?,?,?)",
        (jid, value.chat_id, value.mode, "queued", value.model_dump_json(), now()),
    )
    tasks[jid] = asyncio.create_task(execute_job(jid, value.model_dump()))
    return {"id": jid}


@app.get("/api/runs/{job_id}/events")
async def events(job_id: str, request: Request, after: int = 0):
    try:
        store.job(job_id)
    except KeyError as error:
        raise HTTPException(404, "Task not found.") from error

    async def stream():
        cursor = after
        while not await request.is_disconnected():
            rows = store.execute(
                "SELECT seq,data FROM events WHERE job_id=? AND seq>? ORDER BY seq", (job_id, cursor)
            )
            for row in rows:
                cursor = row["seq"]
                yield f"id: {cursor}\ndata: {row['data']}\n\n"
            if store.job(job_id)["status"] not in ("queued", "running"):
                yield 'data: {"type":"closed"}\n\n'
                return
            if not rows:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.15)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/runs/{job_id}/cancel")
async def cancel(job_id: str):
    task = tasks.get(job_id)
    if task:
        task.cancel()
    else:
        try:
            if store.job(job_id)["status"] == "awaiting_approval":
                store.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
                store.event(job_id, {"type": "cancelled"})
                return {"cancelled": True}
        except KeyError:
            pass
    return {"cancelled": bool(task)}


async def bounded_upload(file, maximum=20_000_000):
    chunks, total = [], 0
    while chunk := await file.read(64 * 1024):
        total += len(chunk)
        if total > maximum:
            raise HTTPException(413, "The file exceeds the 20 MB limit.")
        chunks.append(chunk)
    if not total:
        raise HTTPException(400, "The file is empty.")
    return b"".join(chunks)


@app.post("/api/uploads")
async def upload(file: UploadFile = File()):
    from .media import AUDIO_SUFFIXES, VIDEO_SUFFIXES
    if Path(file.filename or "").suffix.lower() in AUDIO_SUFFIXES | VIDEO_SUFFIXES:
        require_models(setup, "dictation")
    content = await bounded_upload(file)
    name = Path(file.filename or "attachment").name
    uid = uuid4().hex
    path = store.directory / "uploads" / (uid + Path(name).suffix.lower())
    await asyncio.to_thread(path.write_bytes, content)
    try:
        from .media import AUDIO_SUFFIXES, VIDEO_SUFFIXES, video_frames
        suffix = Path(name).suffix.lower()
        details = {"sha256": __import__("hashlib").sha256(content).hexdigest(), "parser_version": "macbot-0.2"}
        if suffix in AUDIO_SUFFIXES:
            kind = "audio"
            async with work_lock:
                transcription = await asyncio.to_thread(transcribe_audio, path, store.setting("settings", {"language": "en"}))
            text = "Audio transcript:\n" + transcription["text"] if transcription["text"] else "No speech was recognised. This is an audio attachment, not a transcript."
            details.update(transcription)
        elif suffix in VIDEO_SUFFIXES:
            kind = "video"
            frames, duration = await asyncio.to_thread(video_frames, path)
            if not frames:
                kind = "audio"
                async with work_lock:
                    transcription = await asyncio.to_thread(transcribe_audio, path, store.setting("settings", {"language": "en"}))
                text = "Audio transcript:\n" + transcription["text"]
                details.update(transcription)
            else:
                text = f"Video attachment: {name}. Duration: {duration:.1f} seconds. Visual analysis uses sampled frames, not every moment."
                details.update({"duration": duration, "frame_timestamps": [time for time, _ in frames]})
                try:
                    async with work_lock:
                        transcription = await asyncio.to_thread(transcribe_audio, path, store.setting("settings", {"language": "en"}))
                    text += "\nAudio transcript:\n" + transcription["text"]
                except Exception as error:
                    details["audio_note"] = f"No usable audio track: {type(error).__name__}"
        else:
            kind, text = await asyncio.to_thread(extract, name, content)
    except Exception as error:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(error)[:300]) from error
    store.execute("INSERT INTO uploads VALUES(?,?,?,?,?)", (uid, name, kind, text, str(path)))
    store.execute("INSERT INTO upload_details VALUES(?,?)", (uid, json.dumps(details)))
    chunks = 0
    if kind in ("document", "audio", "image", "video"):
        try:
            async with work_lock:
                if kind == "document":
                    chunks = await asyncio.to_thread(retrieval.index, uid, name, text)
                else:
                    chunks = await asyncio.to_thread(retrieval.index_media, uid, name, kind, path, text)
            store.execute("INSERT OR REPLACE INTO index_state VALUES(?,?,?)", (uid, chunks, ""))
        except Exception as error:
            logger.exception("Document indexing failed")
            store.execute("INSERT OR REPLACE INTO index_state VALUES(?,?,?)", (uid, 0, str(error)[:300]))
            raise HTTPException(
                503,
                "The file was saved but could not be indexed. Retry indexing or check the local search model.",
            ) from error
        finally:
            await asyncio.to_thread(retrieval.release_models)
    return {"id": uid, "name": name, "kind": kind, "characters": len(text), "chunks": chunks}


@app.get("/api/uploads/{upload_id}")
async def original_upload(upload_id: str):
    rows = store.execute("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if not rows:
        raise HTTPException(404, "File not found.")
    path = Path(rows[0]["path"]).resolve()
    if not path.is_relative_to((store.directory / "uploads").resolve()) or not path.is_file():
        raise HTTPException(404, "File not found.")
    return FileResponse(path, filename=rows[0]["name"])


def transcribe_audio(path, config):
    global voice_model
    from faster_whisper import WhisperModel

    selected = config.get("whisper_model", "base")
    if voice_model is None or voice_model[0] != selected:
        from .assets import prepare_asset
        model_path = str(prepare_asset(store.directory, "whisper")) if selected == "base" else selected
        voice_model = (
            selected,
            WhisperModel(
                model_path,
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
                download_root=str(store.directory / "models"),
            ),
        )
    language = config.get("language", "en")
    segments, info = voice_model[1].transcribe(
        str(path), language=None if language == "auto" else language, vad_filter=True, beam_size=1
    )
    if info.duration > 600:
        raise ValueError("Audio files are limited to ten minutes.")
    return {
        "text": " ".join(s.text.strip() for s in segments).strip(),
        "language": info.language,
        "duration": info.duration,
    }


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File()):
    suffix = Path(file.filename or "dictado.webm").suffix.lower()
    if suffix not in (".webm", ".wav", ".mp3", ".m4a", ".ogg", ".flac", ".mp4"):
        raise HTTPException(400, "Unsupported audio format.")
    content = await bounded_upload(file)
    with tempfile.TemporaryDirectory(prefix="macbot-audio-", dir=store.directory / "uploads") as tmp:
        path = Path(tmp) / ("audio" + suffix)
        path.write_bytes(content)
        try:
            async with work_lock:
                result = await asyncio.to_thread(transcribe_audio, path, store.setting("settings", {}))
        except Exception as error:
            logger.exception("Transcription failed")
            raise HTTPException(400, "The audio could not be transcribed: " + str(error)[:250]) from error
    if not result["text"]:
        raise HTTPException(422, "No speech was detected. Try a clearer recording.")
    return result


@app.post("/api/messages/{message_id}/export/{fmt}")
async def export(message_id: str, fmt: Literal["md", "txt", "docx", "pdf"]):
    rows = store.execute("SELECT content,sources FROM messages WHERE id=?", (message_id,))
    if not rows:
        raise HTTPException(404, "Message not found.")
    text = rows[0]["content"]
    sources = json.loads(rows[0]["sources"])
    if sources:
        text += "\n\n## Sources\n" + "\n".join(f"[{s['id']}] {s['title']} — {s['url']}" for s in sources)
    path = store.directory / "artifacts" / f"macbot-{message_id}.{fmt}"
    await asyncio.to_thread(export_document, text, path, fmt)
    return FileResponse(path, filename=f"MacBot.{fmt}")


app.include_router(feature_routes(sys.modules[__name__]))
app.include_router(setup_routes(sys.modules[__name__]))
app.include_router(privacy_routes(sys.modules[__name__]))
