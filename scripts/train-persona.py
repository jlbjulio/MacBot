import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from macbot.persona import train

print(json.dumps(train(root / "backend/data", steps=32), indent=2), flush=True)
