"""Clean verified obsolete outputs, preferring the Windows Recycle Bin."""
import ctypes
import json
import shutil
import stat
from pathlib import Path

root = Path(__file__).resolve().parents[1]
obsolete = ["build/omitted-rocm-v7-1", "src-tauri/binaries", "src-tauri/target/release/bundle",
    "scripts/finish-english.py", "scripts/build-backend.mjs", "scripts/smoke-packaged.py",
    "scripts/debug-packaged.py", "scripts/prepare-runtime.py", "scripts/evaluate-local.py",
    "scripts/native-ui-check.cjs", "scripts/native-extended-check.cjs", "scripts/native-vision-check.cjs",
    "backend/desktop.spec", "scripts/debug-research-draft.py",
    "release/MacBot_0.1.0_x64-setup.exe", "release/MacBot", "electron",
    "build/qa/bootstrap-20261006", "build/qa/MacBot-0.2.0-Windows-Runtime.zip",
    "build/qa/background-20261006", "logs/quality/native/background-data",
    "release/MacBot-0.2.0-Windows-Runtime.zip",
    "release/MacBot-0.2.0-Windows-Runtime.zip.sha256",
    "release/MacBot-0.2.0-Windows-Portable.zip.sha256",
    "release/MacBot-Portable/bootstrap.json", "release/MacBot-Portable/Data",
    "build/portable-runtime", "build/qa/direct-engines", "build/qa/direct-engines-final",
    "build/qa/compact-portable", "build/bootstrap-downloads", "build/launcher/initial-launcher.pdb",
    "release/MacBot-Portable/MacBot/README.md", "release/MacBot-Portable/MacBot/PRIVACY.md",
    "release/MacBot-Portable/MacBot/START HERE.txt", "release/MacBot-Portable/MacBot/LICENSE",
    "release/MacBot-Portable/MacBot/THIRD-PARTY.json", "src-tauri/icons/android", "src-tauri/icons/ios",
    "src-tauri/icons/64x64.png", "src-tauri/icons/StoreLogo.png"]
obsolete.extend(str(item.relative_to(root)) for item in (root / "src-tauri/icons").glob("Square*Logo.png"))

class Operation(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("function", ctypes.c_uint), ("source", ctypes.c_wchar_p),
                ("target", ctypes.c_wchar_p), ("flags", ctypes.c_ushort), ("aborted", ctypes.c_int),
                ("mappings", ctypes.c_void_p), ("title", ctypes.c_wchar_p)]

report = root / "logs/cleanup-obsolete.json"
removed = json.loads(report.read_text(encoding="utf-8")) if report.exists() else []
for relative in obsolete:
    target = root / relative
    if not target.exists():
        continue
    if relative == "release/MacBot-Portable/Data" and any(target.iterdir()):
        continue
    if relative in ("release/MacBot-Portable/MacBot/LICENSE", "release/MacBot-Portable/MacBot/THIRD-PARTY.json") and not (target.parent / "licenses" / target.name).is_file():
        raise RuntimeError("Preserve the license notice before cleanup: " + str(target))
    resolved = target.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve() or target.is_symlink():
        raise RuntimeError("Unexpected cleanup target: " + str(target))
    if target.is_dir():
        # uv creates version aliases inside its private staging directory.
        for alias in target.glob("runtime/.python-install/*"):
            if alias.lstat().st_file_attributes & 0x400:
                if not alias.resolve(strict=True).is_relative_to(resolved):
                    raise RuntimeError("External Python alias must be preserved: " + str(alias))
                alias.rmdir()
        for item in target.rglob("*"):
            if item.lstat().st_file_attributes & 0x400:
                raise RuntimeError("Linked QA data must not be removed: " + str(item))
    buffer = ctypes.create_unicode_buffer(str(resolved) + "\0\0")
    operation = Operation(None, 3, ctypes.cast(buffer, ctypes.c_wchar_p), None, 0x454, 0, None, None)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result == 120 and target.exists() and relative in (
        "build/qa/direct-engines", "build/qa/direct-engines-final", "build/qa/compact-portable"
    ):
        def clear_readonly(function, path, _, boundary=resolved):
            owned = Path(path).resolve(strict=True)
            if not owned.is_relative_to(boundary):
                raise RuntimeError("Unexpected QA cleanup path: " + str(owned))
            owned.chmod(stat.S_IWRITE)
            function(path)
        shutil.rmtree(resolved, onerror=clear_readonly)
        result = 0
    if result or operation.aborted or target.exists():
        raise RuntimeError(f"Cleanup failed for {relative}: {result}")
    removed.append(relative)
    report.write_text(json.dumps(removed, indent=2), encoding="utf-8")
(root / "logs/cleanup-obsolete.json").write_text(json.dumps(removed, indent=2), encoding="utf-8")
print(f"Cleaned {len(removed)} obsolete targets.")
