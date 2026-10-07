"""Launch or close an isolated native QA window; CDP is enabled only for this process."""
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

root = Path(__file__).resolve().parents[1]
output = root / "logs/quality/native"
output.mkdir(parents=True, exist_ok=True)
state_file = output / "session.json"
portable = Path(os.environ.get("MACBOT_QA_EXE", str(root / "release/MacBot-Portable/MacBot.exe"))).resolve()
assert (portable.is_relative_to((root / "release").resolve()) or portable.is_relative_to((root / "build/qa").resolve())) and portable.name == "MacBot.exe"
installed = Path(os.environ["LOCALAPPDATA"]) / "MacBot/MacBot.exe"
if len(sys.argv) > 1 and sys.argv[1] == "stop":
    state = json.loads(state_file.read_text())
    process = psutil.Process(state["pid"])
    executable = Path(state.get("executable", portable)).resolve()
    assert executable == installed.resolve() or ((executable.is_relative_to((root / "release").resolve()) or executable.is_relative_to((root / "build/qa").resolve())) and executable.name == "MacBot.exe")
    assert Path(process.exe()).resolve() == executable
    assert abs(process.create_time() - state["created"]) < 1
    descendants = process.children(recursive=True)
    state["processes"] = [{"name": p.name(), "pid": p.pid, "rss_mib": round(p.memory_info().rss / 1024**2, 1)} for p in [process, *descendants] if p.is_running()]
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    ctypes.windll.user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
    ctypes.windll.user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    ctypes.windll.user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    ctypes.windll.user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    posted = []
    def close_window(hwnd, _):
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process.pid:
            title = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(hwnd, title, len(title))
            if title.value == "MacBot":
                posted.append({"hwnd": hwnd, "title": title.value, "accepted": bool(ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0))})
        return True
    callback = callback_type(close_window)
    ctypes.windll.user32.EnumWindows(callback, 0)
    graceful = True
    try:
        exit_code = process.wait(timeout=15)
    except psutil.TimeoutExpired:
        graceful = False
        process.terminate()
        exit_code = process.wait(timeout=5)
    _, alive = psutil.wait_procs(descendants, timeout=10)
    state["exit"] = {"parent_closed": True, "graceful": graceful, "exit_code": exit_code, "posted_windows": posted, "remaining_owned_children": [p.pid for p in alive if p.is_running()]}
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state["exit"]))
else:
    directory = output / ("engine-data" if "--engines" in sys.argv else "background-data" if "--background" in sys.argv else "setup-data" if "--setup" in sys.argv else "final-data" if "--final" in sys.argv else "relocated-data" if "--relocated" in sys.argv else "data")
    # Use packaged model files while keeping QA conversations separate.
    (directory / "models").mkdir(parents=True, exist_ok=True)
    source_models = root / "backend/data/models"
    for model in ([] if "--setup" in sys.argv or "--background" in sys.argv else source_models.iterdir()):
        if model.name.startswith(".") or model.name.startswith("models--"):
            continue
        target = directory / "models" / model.name
        if model.is_dir() and not target.exists():
            subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(model)], capture_output=True, check=True)
    chat_target = directory / "models/ollama"
    if "--setup" not in sys.argv and "--background" not in sys.argv and not chat_target.exists():
        subprocess.run(["cmd", "/c", "mklink", "/J", str(chat_target), str(Path.home() / ".ollama/models")], capture_output=True, check=True)
    env = {**os.environ, "MACBOT_DATA_DIR": str(directory), "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--remote-debugging-port=9223"}
    env.pop("MACBOT_TOKEN", None)
    env.pop("MACBOT_API_URL", None)
    executable = installed if "--installed" in sys.argv else portable
    started = time.perf_counter()
    with (output / "app.log").open("w") as log:
        child = subprocess.Popen([str(executable)], env=env, stdout=log, stderr=log)
    inner = executable.parent / "MacBot/MacBot.exe"
    if inner.is_file():
        launched_at = time.time() - 2
        for _ in range(100):
            matches = [p for p in psutil.process_iter(["exe", "create_time"])
                       if p.info["exe"] and Path(p.info["exe"]).resolve() == inner.resolve()
                       and p.info["create_time"] >= launched_at]
            if matches:
                child = matches[0]
                executable = inner
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("The portable launcher did not start its internal app")
    state = {"pid": child.pid, "created": psutil.Process(child.pid).create_time(), "profile": str(directory), "executable": str(executable)}
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    with httpx.Client(timeout=2, trust_env=False) as client:
        for _ in range(160):
            if not psutil.pid_exists(child.pid):
                raise RuntimeError(f"Native app exited; see {output / 'app.log'}")
            try:
                if client.get("http://127.0.0.1:9223/json/version").status_code == 200:
                    state["webview_startup_seconds"] = round(time.perf_counter() - started, 3)
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("WebView2 QA endpoint did not start")
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if "--hidden" in sys.argv:
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        ctypes.windll.user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        ctypes.windll.user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        ctypes.windll.user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        def hide_window(hwnd, _):
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == child.pid:
                title = ctypes.create_unicode_buffer(512)
                ctypes.windll.user32.GetWindowTextW(hwnd, title, len(title))
                if title.value == "MacBot":
                    ctypes.windll.user32.ShowWindow(hwnd, 0)
            return True
        callback = callback_type(hide_window)
        ctypes.windll.user32.EnumWindows(callback, 0)
    print(json.dumps({"pid": child.pid, "webview_startup_seconds": state["webview_startup_seconds"]}))
