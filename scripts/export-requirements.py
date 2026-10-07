"""Export hashed Windows dependencies without allowing GPU wheel substitution."""
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
    torch = next(package for package in lock["package"]
                 if package["name"] == "torch" and package["version"].endswith("+cpu"))
    wheel = next(wheel for wheel in torch["wheels"]
                 if "cp311-cp311-win_amd64.whl" in wheel["url"])
    text = destination.read_text(encoding="utf-8")
    text = re.sub(r"^--extra-index-url .*\n", "", text, flags=re.MULTILINE)
    text, count = re.subn(
        r"^torch==[^\n]*\+cpu[^\n]*\n(?:    --hash=[^\n]*\n)+",
        lambda _: f"torch @ {wheel['url']} \\\n    --hash={wheel['hash']}\n",
        text, flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError("The locked Windows CPU Torch requirement was not found exactly once.")
    destination.write_text(text, encoding="utf-8", newline="\n")
    pins = ROOT / "bootstrap/engines.json"
    manifest = json.loads(pins.read_text(encoding="utf-8"))
    manifest["requirements_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
    pins.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    print("Exported pinned Windows CPU requirements and updated their checksum.")


if __name__ == "__main__":
    export()
