import json
import os
from pathlib import Path
import subprocess
import sys
import time


def engine_path():
    portable = Path(sys.executable).parent.parent / "diffusion/sd-cli.exe"
    if portable.is_file():
        return portable
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MacBot/Development/diffusion/sd-cli.exe"


def render(prompt, path, directory, width, height, seed, stop=None):
    from .assets import prepare_asset
    from .native_assets import creation_manifest
    executable = engine_path()
    if not executable.is_file():
        raise ValueError("The image engine is missing. Retry preparation in Settings.")
    model = prepare_asset(directory, "image")
    marker = json.loads((model / "macbot-model.json").read_text(encoding="utf-8"))
    profile = marker["profile"]
    edge = 768 if profile == "compact" else 1024
    scale = min(1, edge / max(width, height))
    native_width = max(128, round(width * scale / 32) * 32)
    native_height = max(128, round(height * scale / 32) * 32)
    recipe = creation_manifest("image")["recipe"]
    temporary = path.with_name(path.stem + ".render.png")
    arguments = [str(executable), "--diffusion-model", str(model / f"diffusion-{profile}.gguf"),
        "--llm", str(model / "encoder.gguf"), "--vae", str(model / "vae.safetensors"),
        "-p", prompt, "-o", str(temporary), "-W", str(native_width), "-H", str(native_height),
        "--steps", str(recipe["steps"]), "--cfg-scale", str(recipe["cfg"]),
        "--sampling-method", "euler",
        "--seed", str(seed), "--mmap", "--params-backend", "disk", "--backend", "vae=cpu",
        "--max-vram", "-0.5", "--vae-tiling", "--diffusion-fa", "--log-level", "warn",
        "--threads", str(max(1, min(6, (os.cpu_count() or 4) // 2)))]
    if os.environ.get("MACBOT_IMAGE_CPU") == "1":
        arguments[arguments.index("vae=cpu")] = "cpu"
    directory.joinpath("logs").mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    process = None
    completed = False
    try:
        with (directory / "logs/image-inference.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(arguments, cwd=executable.parent, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            while process.poll() is None:
                if stop is not None and stop.is_set():
                    raise InterruptedError("Image creation stopped.")
                if time.monotonic() - started > 900:
                    raise ValueError("Image creation exceeded its time limit. Try a smaller image or close other applications.")
                time.sleep(0.1)
            if process.returncode or not temporary.is_file():
                raise ValueError("The image engine could not render this request. Free memory and retry; details are in the local image log.")
        completed = True
        return temporary, {"native_width": native_width, "native_height": native_height, "seed": seed,
            "profile": profile, "model": "ERNIE-Image-Turbo", "steps": recipe["steps"],
            "upscaled": native_width != width or native_height != height,
            "render_seconds": round(time.monotonic() - started, 3)}
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if not completed:
            temporary.unlink(missing_ok=True)
