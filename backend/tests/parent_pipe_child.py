import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from macbot.lifecycle import parent_signal

closed = threading.Event()
def watch():
    parent_signal()
    closed.set()
threading.Thread(target=watch, daemon=True).start()
import scipy.linalg  # noqa: E402,F401
from diffusers.pipelines.stable_diffusion.pipeline_stable_diffusion import StableDiffusionPipeline  # noqa: E402,F401
print("LIBRARIES_READY", flush=True)
if not closed.wait(60):
    raise SystemExit("The parent signal was missed.")
