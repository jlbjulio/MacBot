import queue
import subprocess
import sys
import threading
from pathlib import Path


def test_open_parent_pipe_does_not_deadlock_late_dll_imports(tmp_path):
    child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("parent_pipe_child.py"))], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=(tmp_path / "imports.log").open("w"), text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    output = queue.Queue()
    assert child.stdout is not None and child.stdin is not None
    stdout, stdin = child.stdout, child.stdin
    threading.Thread(target=lambda: output.put(stdout.readline()), daemon=True).start()
    try:
        assert output.get(timeout=45).strip() == "LIBRARIES_READY"
        stdin.write("shutdown\n")
        stdin.flush()
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        stdin.close()
        stdout.close()
