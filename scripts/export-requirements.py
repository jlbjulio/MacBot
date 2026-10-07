"""Export hashed Windows dependencies without allowing Torch wheel substitution."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def export():
    destination = ROOT / "bootstrap/requirements.txt"
    subprocess.run([
        "uv", "export", "--directory", str(ROOT / "backend"), "--locked",
        "--no-dev", "--no-emit-project", "--no-emit-package", "antlr4-python3-runtime",
        "--emit-index-url", "--output-file", str(destination), "--quiet",
    ], check=True, capture_output=True)
    lock = tomllib.loads((ROOT / "backend/uv.lock").read_text(encoding="utf-8"))
    text = destination.read_text(encoding="utf-8")
    text = re.sub(r"^--extra-index-url .*\n", "", text, flags=re.MULTILINE)
    for name in ("torch", "torchvision"):
        package = next(package for package in lock["package"] if package["name"] == name
                       and any("cp311-cp311-win_amd64.whl" in wheel["url"] for wheel in package.get("wheels", [])))
        if package["source"].get("registry") != "https://download.pytorch.org/whl/cpu":
            raise RuntimeError("The locked Torch build must be CPU-only; llama.cpp handles chat acceleration.")
        wheel = next(wheel for wheel in package["wheels"]
                     if "cp311-cp311-win_amd64.whl" in wheel["url"])
        replacement = f"{name} @ {wheel['url']} \\\n    --hash={wheel['hash']}\n"
        text, count = re.subn(
            rf"^{name}==[^\n]*\n(?:    --hash=[^\n]*\n)+",
            "",
            text, flags=re.MULTILINE,
        )
        if not count:
            raise RuntimeError(f"The locked Windows {name} requirement was not found.")
        text += replacement
    destination.write_text(text, encoding="utf-8", newline="\n")
    pins = ROOT / "bootstrap/engines.json"
    manifest = json.loads(pins.read_text(encoding="utf-8"))
    manifest["requirements_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
    pins.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    print("Exported pinned Windows CPU requirements and updated their checksum.")


if __name__ == "__main__":
    export()
