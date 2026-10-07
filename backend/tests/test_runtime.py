import pytest

from macbot.runtime import Ollama


@pytest.mark.asyncio
async def test_remote_or_missing_alias_cannot_reach_inference(monkeypatch):
    runtime = Ollama()

    async def models():
        return {"available": True, "models": [{"name": "qwen3.5:4b"}]}

    monkeypatch.setattr(runtime, "models", models)
    with pytest.raises(ValueError, match="unavailable locally"):
        async for _ in runtime.stream("alias-de-modelo-remoto", [{"role": "user", "content": "hola"}]):
            pytest.fail("Remote alias produced inference output")
