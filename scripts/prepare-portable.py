"""Prepare the small portable payload; engines are installed on first launch."""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTABLE = ROOT / "release/MacBot-Portable"
TARGET = PORTABLE / "MacBot"
PAYLOAD = ROOT / "build/bootstrap-payload"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resolve_uv(manifest):
    candidates = [TARGET / "uv.exe", ROOT / "build/tools/uv.exe"]
    system_uv = shutil.which("uv")
    if system_uv:
        candidates.append(Path(system_uv))
    for candidate in candidates:
        if candidate.is_file() and digest(candidate) == manifest["uv_sha256"]:
            return candidate
    raise RuntimeError(
        "No uv binary matches bootstrap/engines.json. Restore the pinned executable "
        "in release/MacBot-Portable/MacBot/uv.exe or place it in build/tools/uv.exe."
    )


def prepare():
    TARGET.mkdir(parents=True, exist_ok=True)
    if TARGET.is_symlink() or TARGET.resolve().parent != PORTABLE.resolve():
        raise RuntimeError("Unexpected support folder.")
    # Migrate the previous layout without discarding private data or prepared engines.
    if not (TARGET / "MacBot.exe").exists() and (PORTABLE / "MacBot.exe").exists():
        shutil.move(PORTABLE / "MacBot.exe", TARGET / "MacBot.exe")
    for existing in list(PORTABLE.iterdir()):
        if existing.name in {"MacBot", "MacBot.exe"}:
            continue
        if existing.name in {"Data", "runtime", "bootstrap", "uv.exe", "portable.marker", "START HERE.txt",
                             "README.md", "PRIVACY.md", "LICENSE", "MICROSOFT-RUNTIME.txt", "THIRD-PARTY.json"} or existing.suffix == ".dll":
            if (TARGET / existing.name).exists():
                raise RuntimeError("Both layouts contain " + existing.name + "; preserve them before merging.")
            if existing.name == "runtime":
                for alias in existing.glob(".python-install/*"):
                    if alias.lstat().st_file_attributes & 0x400:
                        if not alias.resolve(strict=True).is_relative_to(existing.resolve()):
                            raise RuntimeError("Preserve an external Python alias: " + str(alias))
                        alias.rmdir()
            shutil.move(existing, TARGET / existing.name)
    PAYLOAD.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "bootstrap/engines.json").read_text(encoding="utf-8"))
    uv = resolve_uv(manifest)
    if uv.resolve() != (TARGET / "uv.exe").resolve():
        shutil.copy2(uv, TARGET / "uv.exe")
    app = PAYLOAD / "app"
    (app / "macbot").mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "backend/macbot").glob("*.py"):
        shutil.copy2(source, app / "macbot" / source.name)
    for stale in (app / "ollama-model.json", app / "macbot/persona.py",
                  TARGET / "bootstrap/app/ollama-model.json", TARGET / "bootstrap/app/macbot/persona.py"):
        stale.unlink(missing_ok=True)
    for name in ("desktop_entry.py", "model-manifest.json", "chat-model.json"):
        shutil.copy2(ROOT / "backend" / name, app / name)
    site = PAYLOAD / "site-packages"
    for name in ("antlr4", "antlr4_python3_runtime-4.9.3.dist-info"):
        shutil.copytree(Path(sys.executable).parents[1] / "Lib/site-packages" / name, site / name,
                        dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "bootstrap/requirements.txt", PAYLOAD / "requirements.txt")
    if digest(PAYLOAD / "requirements.txt") != manifest["requirements_sha256"]:
        raise RuntimeError("The requirements and engine pins do not match.")
    for license_file in (ROOT / "bootstrap").glob("*-LICENSE*.txt"):
        shutil.copy2(license_file, PAYLOAD / license_file.name)
    manifest["payload_files"] = [{"path": p.relative_to(PAYLOAD).as_posix(), "bytes": p.stat().st_size,
                                  "sha256": digest(p)} for p in sorted(PAYLOAD.rglob("*"))
                                 if p.is_file() and p.name != "manifest.json"]
    (PAYLOAD / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    shutil.copytree(PAYLOAD, TARGET / "bootstrap", dirs_exist_ok=True)
    redist = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/2022/BuildTools/VC/Redist/MSVC"
    versions = sorted((p for p in redist.glob("*") if p.name[0].isdigit()), key=lambda p: tuple(map(int, p.name.split("."))))
    if not versions:
        raise RuntimeError("Visual C++ app-local redistributable files are required for the desktop build.")
    for library in (versions[-1] / "x64/Microsoft.VC143.CRT").glob("*.dll"):
        shutil.copy2(library, TARGET / library.name)
    (TARGET / "portable.marker").write_text("MacBot portable 0.3\n", encoding="utf-8")
    notices = TARGET / "licenses"
    notices.mkdir(parents=True, exist_ok=True)
    (notices / "MICROSOFT-RUNTIME.txt").write_text(
        f"Visual C++ app-local runtime {versions[-1].name}. Microsoft Software License Terms apply.\n"
        "Source: licensed Visual Studio Build Tools redistributable directory.\n"
        "https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files\n"
        "Local deployment requires updating these files with MacBot releases.\n",
        encoding="utf-8")
    print(json.dumps({"payload_files": len(manifest["payload_files"]), "engine_downloads": "direct from upstream", "portable": str(TARGET)}))


if __name__ == "__main__":
    prepare()
