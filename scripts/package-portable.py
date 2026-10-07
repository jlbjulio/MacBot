import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "release/MacBot-Portable"
support = source / "MacBot"
version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
archive = root / "release" / f"MacBot-{version}-Windows-Portable.zip"
verify_existing = "--verify-existing" in sys.argv
working = archive if verify_existing else archive.with_suffix(".zip.partial")
if not (source / "MacBot.exe").exists():
    raise SystemExit("Build the desktop executable first.")
if not (support / "bootstrap/manifest.json").is_file() or not (support / "uv.exe").is_file():
    raise SystemExit("Run scripts/prepare-portable.py before packaging the portable.")
if {item.name for item in source.iterdir()} != {"MacBot.exe", "MacBot"}:
    raise SystemExit("The portable root must contain only MacBot.exe and the MacBot support folder.")
if not all((support / "licenses" / name).is_file() for name in ("LICENSE", "MICROSOFT-RUNTIME.txt", "THIRD-PARTY.json")):
    raise SystemExit("Run scripts/write-notices.py before packaging; license notices are required.")
subprocess.run([str(source / "MacBot.exe"), "--verify-package"], check=True, capture_output=True)
allowed = {"MacBot.exe", "uv.exe", "portable.marker"}
if not verify_existing:
    with zipfile.ZipFile(working, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
        files = [source / "MacBot.exe"]
        files.extend(item for item in support.iterdir() if item.is_file() and (item.name in allowed or item.suffix == ".dll"))
        files.extend(p for p in (support / "bootstrap").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
        files.extend(p for p in (support / "licenses").iterdir() if p.is_file())
        for item in sorted(files):
            if item.is_symlink():
                raise RuntimeError("Do not package linked launcher files: " + str(item))
            bundle.write(item, Path("MacBot-Portable") / item.relative_to(source))
with zipfile.ZipFile(working, "r") as bundle:
    if any({"Data", "runtime", ".runtime-stage"}.intersection(Path(name).parts) for name in bundle.namelist()):
        raise RuntimeError("The launcher archive contains workspace data or engine files.")
    damaged = bundle.testzip()
    if damaged:
        raise RuntimeError("Archive verification failed: " + damaged)
digest = hashlib.sha256()
with working.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
if not verify_existing:
    working.replace(archive)
checksum = root / "logs/quality/MacBot-Portable.sha256"
checksum.parent.mkdir(parents=True, exist_ok=True)
temporary_checksum = checksum.with_suffix(".sha256.partial")
temporary_checksum.write_text(digest.hexdigest() + "  " + archive.name + "\n", encoding="ascii")
temporary_checksum.replace(checksum)
print(json.dumps({"archive": str(archive), "gib": round(archive.stat().st_size / 1024**3, 3), "sha256": digest.hexdigest()}))
