import asyncio

import pytest
import psutil

from macbot.runtime import LlamaRuntime


@pytest.mark.asyncio
async def test_remote_or_missing_alias_cannot_reach_inference(tmp_path, monkeypatch):
    runtime = LlamaRuntime(tmp_path)

    async def models():
        return {"available": True, "models": [{"name": "macbot-4b"}]}

    monkeypatch.setattr(runtime, "models", models)
    with pytest.raises(ValueError, match="Remote and arbitrary"):
        async for _ in runtime.stream("alias-de-modelo-remoto", [{"role": "user", "content": "hola"}]):
            pytest.fail("Remote alias produced inference output")


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_interrupted_reply_stops_owned_worker(tmp_path, monkeypatch, cancel):
    runtime = LlamaRuntime(tmp_path)
    worker = tmp_path / "worker.py"
    worker.write_text(
        'import json, sys, time, runpy\n'
        f'runpy.run_path({str(runtime.worker)!r})["configure_stdio"]()\n'
        'print(json.dumps({"ready": True}), flush=True)\n'
        'for line in sys.stdin:\n'
        ' print(json.dumps({"token": "hello — café"}, ensure_ascii=False), flush=True)\n'
        ' time.sleep(60)\n', encoding="utf-8")
    runtime.worker = worker

    async def models():
        return {"available": True}

    monkeypatch.setattr(runtime, "models", models)
    response = runtime.stream("macbot-4b", [{"role": "user", "content": "hello"}])
    assert await anext(response) == "hello — café"
    process = runtime.process
    assert process is not None
    descendants = psutil.Process(process.pid).children(recursive=True)
    if cancel:
        reading = asyncio.create_task(anext(response))
        await asyncio.sleep(0.05)
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading
    else:
        await response.aclose()
    assert process.poll() is not None
    assert runtime.process is None and runtime.log is None
    assert not any(child.is_running() for child in descendants)
