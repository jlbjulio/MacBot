"""Own the native GGUF server; exchange only final answers over parent-owned pipes."""
import base64
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time

REASONING_BUDGETS = {"low": 128, "medium": 256, "high": 512}


def configure_stdio():
    for channel in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(channel, io.TextIOWrapper):
            channel.reconfigure(encoding="utf-8")


def engine_path():
    if os.environ.get("MACBOT_LLAMA_SERVER"):
        return Path(os.environ["MACBOT_LLAMA_SERVER"])
    portable = Path(sys.executable).parent.parent / "llama/llama-server.exe"
    if portable.is_file():
        return portable
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MacBot/Development/llama/llama-server.exe"


def normalize_messages(messages):
    from PIL import Image
    result = []
    for item in messages:
        if item.get("role") not in ("system", "user", "assistant") or not isinstance(item.get("content"), str):
            raise ValueError("The conversation contains an invalid message.")
        images = item.get("images", [])
        if len(images) > 25:
            raise ValueError("Use fewer images in this conversation.")
        parts = [{"type": "text", "text": item["content"]}]
        for encoded in images:
            if len(encoded) > 28 * 1024**2:
                raise ValueError("This image is too large for local inference.")
            with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as image:
                if image.width * image.height > 16_000_000:
                    raise ValueError("Use an image with at most 16 megapixels.")
                image.thumbnail((768, 768))
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="JPEG", quality=90)
            uri = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": uri}})
        result.append({"role": item["role"], "content": parts if images else item["content"]})
    return result


def completion_body(request):
    effort = request.get("reasoning", "medium")
    if effort not in REASONING_BUDGETS:
        raise ValueError("Choose Low, Medium or High thinking.")
    maximum = request.get("max_tokens", 1800)
    if not isinstance(maximum, int) or not 1 <= maximum <= 6000:
        raise ValueError("The response budget must be between 1 and 6000 tokens.")
    body = {"model": "macbot-4b", "messages": normalize_messages(request["messages"]),
            "stream": True, "max_tokens": maximum + REASONING_BUDGETS[effort],
            "reasoning_effort": effort, "reasoning_budget_tokens": REASONING_BUDGETS[effort],
            "temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
            "cache_prompt": True, "chat_template_kwargs": {"enable_thinking": True}}
    schema = request.get("json_mode")
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}
        if not isinstance(schema, dict):
            body["response_format"] = {"type": "json_object"}
    return body


def main():
    configure_stdio()
    import httpx
    import psutil

    source = Path(sys.argv[1])
    parent = psutil.Process(os.getppid())
    parent_started = parent.create_time()
    server = None
    stopping = threading.Event()

    def stop_server():
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

    def watch_parent():
        while not stopping.wait(1):
            try:
                if parent.is_running() and parent.create_time() == parent_started:
                    continue
            except psutil.Error:
                pass
            stop_server()
            os._exit(0)

    threading.Thread(target=watch_parent, daemon=True, name="inference-parent").start()

    def emit(value):
        sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    try:
        executable = engine_path()
        if not executable.is_file():
            raise ValueError("The local chat engine is missing. Retry preparation in Settings.")
        key = secrets.token_hex(32)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        arguments = [str(executable), "--model", str(source / "Qwen3.5-4B-Q4_K_M.gguf"),
            "--mmproj", str(source / "mmproj-F16.gguf"), "--no-mmproj-offload", "--alias", "macbot-4b",
            "--host", "127.0.0.1", "--port", str(port), "--ctx-size", "8192", "--parallel", "1",
            "--threads", str(max(1, min(6, (os.cpu_count() or 4) // 2))), "--batch-size", "256",
            "--ubatch-size", "128", "--jinja", "--reasoning", "on", "--reasoning-format", "deepseek",
            "--reasoning-budget", "256", "--no-reasoning-preserve", "--no-context-shift",
            "--no-webui", "--offline", "--log-verbosity", "2"]
        # The key is passed through the environment, never exposed in process arguments or logs.
        environment = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_")}
        environment["LLAMA_API_KEY"] = key
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {key}"},
                          timeout=httpx.Timeout(300, connect=5), trust_env=False) as client:
            modes = ["0"] if os.environ.get("MACBOT_CHAT_CPU") == "1" else ["auto", "0"]
            ready = False
            for layers in modes:
                server = subprocess.Popen(arguments + ["--n-gpu-layers", layers], cwd=executable.parent,
                    stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr,
                    env=environment, creationflags=flags)
                deadline = time.monotonic() + 180
                ready = False
                while time.monotonic() < deadline and server.poll() is None:
                    try:
                        if client.get("/health", timeout=2).status_code == 200:
                            ready = True
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                if ready:
                    break
                stop_server()
            if not ready:
                raise ValueError("The local model could not load. Free memory and retry, or check the inference log.")
            emit({"ready": True})
            for line in sys.stdin:
                started = time.monotonic()
                try:
                    request = json.loads(line)
                    body = completion_body(request)
                    received = False
                    with client.stream("POST", "/v1/chat/completions", json=body) as response:
                        if response.status_code != 200:
                            response.read()
                            raise ValueError("The model could not process this request. Try a shorter conversation or fewer attachments.")
                        for event in response.iter_lines():
                            if not event.startswith("data: "):
                                continue
                            data = event[6:]
                            if data == "[DONE]":
                                break
                            frame = json.loads(data)
                            if frame.get("error"):
                                raise ValueError("Local generation failed. Retry with a smaller task.")
                            choices = frame.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            # Private reasoning stays inside the native server and is never persisted or forwarded.
                            if delta.get("reasoning_content"):
                                emit({"progress": True})
                            token = delta.get("content")
                            if token:
                                received = received or bool(token.strip())
                                emit({"token": token})
                            if choices[0].get("finish_reason") == "length":
                                raise ValueError("The response reached its limit. Ask for a shorter result or split the task.")
                    if not received:
                        raise ValueError("The model did not produce an answer. Try again with a smaller task.")
                    emit({"done": True})
                    print(f"generation_seconds={time.monotonic() - started:.3f} reasoning={request.get('reasoning')}",
                          file=sys.stderr, flush=True)
                except ValueError as error:
                    emit({"error": str(error)})
                except (KeyError, TypeError, OSError, httpx.HTTPError) as error:
                    print(f"generation_error={type(error).__name__}", file=sys.stderr, flush=True)
                    emit({"error": "The local model could not complete this task. Try a shorter request or fewer attachments."})
    except (ValueError, OSError, subprocess.SubprocessError):
        emit({"error": "The local chat engine could not start. Retry preparation or check the inference log."})
    finally:
        stopping.set()
        stop_server()


if __name__ == "__main__":
    main()
