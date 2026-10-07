import json
import sys
import threading
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from macbot.setup import download_asset, download_file

output = root / "logs/quality/setup-smoke"
output.mkdir(parents=True, exist_ok=True)
manifest = json.loads((root / "backend/ollama-model.json").read_text())
configuration = manifest["config"]
started = time.perf_counter()
download_file("https://registry.ollama.ai/v2/library/qwen3.5/blobs/" + configuration["digest"],
              output / "chat-config.json", configuration["size"], configuration["digest"].split(":")[1], lambda *args: None, threading.Event())
voice = download_asset(output, "voice")
print(json.dumps({"seconds": round(time.perf_counter() - started, 3), "chat_config_verified": True,
    "voice_download_verified": True, "voice_files": json.loads((voice / "macbot-model.json").read_text())["files"]}))
