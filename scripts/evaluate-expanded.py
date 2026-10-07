import json
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from macbot.evidence import review_claims
from macbot.retrieval import RetrievalIndex

output = root / "logs/quality/expanded"
output.mkdir(parents=True, exist_ok=True)
data = output / "data"
data.mkdir(exist_ok=True)
if not (data / "models").exists():
    import subprocess
    subprocess.run(["cmd", "/c", "mklink", "/J", str(data / "models"), str(root / "backend/data/models")], capture_output=True, check=True)
documents = {
    "refund": "The Luna plan permits a refund within 17 days after purchase.",
    "deployment": "The Orchid project is deployed in Panama and uses port 7318 for monitoring.",
    "backup": "The Cobalt team makes daily backups at 03:40 UTC. Files are retained for 29 days.",
    "catalog": "The Aurora band's catalog contains 11 files organised into jazz, folk and rap.",
}
queries = [("How many days can I request a Luna refund?", "refund"), ("What is Orchid's monitoring port?", "deployment"),
           ("When does Cobalt perform backups?", "backup"), ("How long are backup files retained?", "backup"),
           ("How many files are in Aurora's catalog?", "catalog"), ("Which genres does Aurora organise?", "catalog"),
           ("Where is Orchid deployed?", "deployment"), ("Cuantos dias tengo para devolver Luna?", "refund")]
index = RetrievalIndex(data, engine="gemma2")
started = time.perf_counter()
for identifier, text in documents.items():
    index.index(identifier, identifier + ".txt", text)
indexing = time.perf_counter() - started
results = []
for query, expected in queries:
    started = time.perf_counter()
    hits = index.search(query, list(documents))
    ranking = [item["upload_id"] for item in hits]
    results.append({"query": query, "expected": expected, "ranking": ranking,
                    "seconds": round(time.perf_counter() - started, 4)})
assert all(expected in [h["upload_id"] for h in index.search("Luna refund", [expected])] for expected in documents)
index.release_models()
index.close()
sources = [{"id": 1, "status": "read", "text": "The Luna plan permits a refund within 17 days after purchase."},
           {"id": 2, "status": "read", "text": "The storage path must be a directory. Two clients must not open the same directory concurrently."},
           {"id": 3, "status": "read", "text": "The team retains backups for 29 days."}]
cases = [
    ({"claim": "Luna refunds are available within 17 days of purchase.", "source": 1, "quote": sources[0]["text"]}, True),
    ({"claim": "Luna refunds are available within 71 days of purchase.", "source": 1, "quote": sources[0]["text"]}, False),
    ({"claim": "The storage path must be a directory.", "source": 2, "quote": "The storage path must be a directory."}, True),
    ({"claim": "The storage path must be a file.", "source": 2, "quote": "The storage path must be a directory."}, False),
    ({"claim": "Two clients can safely open the same directory concurrently.", "source": 2, "quote": "Two clients must not open the same directory concurrently."}, False),
    ({"claim": "The team retains backups for 29 days.", "source": 3, "quote": sources[2]["text"]}, True),
    ({"claim": "The team retains backups for 29 days.", "source": 3, "quote": "The team retains backups for 29 months."}, False),
]
review = []
for claim, expected in cases:
    accepted, _ = review_claims({"claims": [claim]}, sources, data)
    review.append({"claim": claim["claim"], "expected": expected, "accepted": bool(accepted), "support": accepted[0]["support"] if accepted else None})
report = {"retrieval": results, "indexing_seconds": round(indexing, 3),
          "recall_at_3": sum(r["expected"] in r["ranking"][:3] for r in results) / len(results),
          "mrr": sum(1 / (r["ranking"].index(r["expected"]) + 1) for r in results) / len(results),
          "evidence_review": review, "review_accuracy": sum(r["expected"] == r["accepted"] for r in review) / len(review),
          "limitations": "Small hand-written regression set; not a calibrated general-purpose factuality benchmark."}
(output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
