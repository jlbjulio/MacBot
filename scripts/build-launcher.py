"""Build a small Windows launcher with no adjacent runtime DLLs."""
import os
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
build = root / "build/launcher"
build.mkdir(parents=True, exist_ok=True)
kits = Path(os.environ["ProgramFiles(x86)"]) / "Windows Kits/10/bin"
rc = max(kits.glob("10.*/x64/rc.exe"), key=lambda path: tuple(map(int, path.parts[-3].split("."))))
resource = build / "launcher.rc"
resource.write_text('1 ICON "' + (root / "src-tauri/icons/icon.ico").as_posix() + '"\n', encoding="utf-8")
with (root / "logs/build-launcher.log").open("w") as log:
    subprocess.run([str(rc), "/nologo", "/fo", str(build / "launcher.res"), str(resource)],
                   check=True, stdout=log, stderr=log)
    subprocess.run([str(Path.home() / ".cargo/bin/rustc.exe"), str(root / "scripts/portable-launcher.rs"),
                    "--edition=2021", "--target=x86_64-pc-windows-msvc", "-C", "target-feature=+crt-static",
                    "-C", "opt-level=s", "-C", "panic=abort", "-C", "strip=symbols",
                    "-C", "link-arg=" + str(build / "launcher.res"),
                    "-o", str(build / "MacBot.exe")],
                   check=True, stdout=log, stderr=log)
shutil.copy2(build / "MacBot.exe", root / "release/MacBot-Portable/MacBot.exe")
print("Built the standalone portable launcher; log: logs/build-launcher.log")
