import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from macbot.persona import preserves

path = root / "backend/data/models/persona-adapter/evaluation.json"
configuration = path.with_name("adapter_config.json")
adapter = json.loads(configuration.read_text(encoding="utf-8"))
adapter["base_model_name_or_path"] = "Qwen/Qwen3-0.6B"
configuration.write_text(json.dumps(adapter, indent=2), encoding="utf-8")
report = json.loads(path.read_text(encoding="utf-8"))
for item in report["evaluation"]:
    item["base_preserves"] = preserves(item["input"], item["base"])
    item["adapter_preserves"] = preserves(item["input"], item["adapter"])
report["evaluator_version"] = "2: numeric equivalence, exact numeric boundaries, curly apostrophes"
report["passed"] = all(item["adapter_preserves"] for item in report["evaluation"])
path.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({"passed": report["passed"], "base": sum(x["base_preserves"] for x in report["evaluation"]), "adapter": sum(x["adapter_preserves"] for x in report["evaluation"])}))
