import json
import time
from pathlib import Path

import psutil

root = Path(__file__).resolve().parents[1]
directory = root / "logs/quality/native"
state = json.loads((directory / "session.json").read_text())
parent = psutil.Process(state["pid"])
samples = []
for _ in range(90):
    try:
        processes = [parent, *parent.children(recursive=True)]
        values = []
        for process in processes:
            try:
                values.append({"name": process.name(), "rss_mib": round(process.memory_info().rss / 1024**2, 1)})
            except psutil.NoSuchProcess:
                pass
        samples.append({"time": time.time(), "processes": values, "available_mib": round(psutil.virtual_memory().available / 1024**2, 1)})
        time.sleep(2)
    except psutil.NoSuchProcess:
        break
report = {"samples": samples, "peak_aggregate_rss_mib": max(sum(p["rss_mib"] for p in item["processes"]) for item in samples),
          "minimum_available_mib": min(item["available_mib"] for item in samples),
          "notes": "Aggregate RSS includes shared pages across owned processes. Sampling covers this QA period, not every workload."}
(directory / "memory-sampling.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({key: value for key, value in report.items() if key != "samples"}))
