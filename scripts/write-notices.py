"""Inventory exact packaged versions and retained license/source metadata."""
import importlib.metadata
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / "release/MacBot-Portable/MacBot"
notices = target / "licenses"
notices.mkdir(parents=True, exist_ok=True)
engines = json.loads((root / "bootstrap/engines.json").read_text(encoding="utf-8"))
pinned_names = {match.group(1).lower().replace("_", "-")
                for match in re.finditer(r"^([A-Za-z0-9_.-]+)(?:==| @ )",
                                         (root / "bootstrap/requirements.txt").read_text(), re.MULTILINE)}
packages = []
for distribution in importlib.metadata.distributions(path=[str(root / "backend/.venv/Lib/site-packages")]):
    metadata = distribution.metadata
    if metadata["Name"].lower().replace("_", "-") not in pinned_names:
        continue
    packages.append({"package": metadata["Name"], "version": distribution.version,
                     "license": (metadata.get_all("License-Expression") or [""])[0] or (metadata.get_all("License") or [""])[0] or "See retained package notices",
                     "source_urls": metadata.get_all("Project-URL") or metadata.get_all("Home-page") or [""],
                     "license_files": [str(file) for file in (distribution.files or []) if "license" in str(file).lower() or "copying" in str(file).lower()]})
lock = json.loads((root / "package-lock.json").read_text())
frontend = [{"package": name.removeprefix("node_modules/"), "version": info.get("version"),
             "license": info.get("license", "See package notices"), "source": info.get("resolved", "")}
            for name, info in lock["packages"].items() if name and not info.get("dev")]
versions = {item["name"].title(): item["version"] for item in engines["components"]}
notice = {"models": json.loads((root / "backend/model-manifest.json").read_text()),
          "chat_model": {"name": "qwen3.5:4b", "license": "Apache-2.0", "source": "https://ollama.com/library/qwen3.5"},
          "downloaded_python_packages": sorted(packages, key=lambda value: value["package"].lower()), "frontend_packages": frontend,
          "runtimes": {**versions, "Python": "3.11.16", "Tauri": "2 (versions pinned in src-tauri/Cargo.lock)"},
          "notes": ["Engines, Python libraries and models are prepared on first launch and retain their own licenses.",
                    "Tiny-SD uses CreativeML OpenRAIL-M, not an OSI software license.",
                    "Python libraries, including Piper, are installed directly from their publishers on first launch; they are not bundled in this ZIP.",
                    "The bundled uv 0.12.21 preparation tool is MIT/Apache-2.0; its license texts are under bootstrap.",
                    "ANTLR Python runtime 4.9.3 source is bundled under BSD-3-Clause; its license text is under bootstrap.",
                    "Python package license files are retained in runtime/python/Lib/site-packages; Node and Ollama retain their notices.",
                    "Version and license metadata is an inventory, not a legal determination."]}
(notices / "THIRD-PARTY.json").write_text(json.dumps(notice, indent=2), encoding="utf-8")
(notices / "LICENSE").write_bytes((root / "LICENSE").read_bytes())
old_microsoft_notice = target / "MICROSOFT-RUNTIME.txt"
if old_microsoft_notice.is_file():
    old_microsoft_notice.replace(notices / old_microsoft_notice.name)
print(json.dumps({"python_packages": len(packages), "frontend_packages": len(frontend), "runtimes": versions}))
