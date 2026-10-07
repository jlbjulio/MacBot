"""Exercise a pinned public stdio server using only the bundled Node runtime."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "build/portable-runtime/runtime/python/app"))
import httpx
from macbot.mcp_client import inspect_server

directory = root / "logs/quality/public-mcp"
(directory / "workspace").mkdir(parents=True, exist_ok=True)
os.environ["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
with httpx.Client(timeout=15, trust_env=False) as client:
    metadata = client.get("https://registry.npmjs.org/@modelcontextprotocol/server-memory/latest").raise_for_status().json()
package = "@modelcontextprotocol/server-memory@" + metadata["version"]
(directory / "package.json").write_text(json.dumps({"package": package, "publisher": metadata.get("repository")}), encoding="utf-8")
config = {"name": "Public memory smoke", "transport": "stdio", "command": "npx", "args": ["--yes", package],
          "env": {"MEMORY_FILE_PATH": str(directory / "workspace/memory.json")}, "headers": {}, "trusted": True}
started = time.perf_counter()
tools = asyncio.run(inspect_server(config, directory))
if not any(tool["name"] == "read_graph" for tool in tools):
    raise RuntimeError("The public MCP server did not advertise its expected tool.")
print(json.dumps({"package": package, "seconds": round(time.perf_counter() - started, 3), "tools": [tool["name"] for tool in tools],
                  "system_node_removed_from_path": True, "publisher": metadata.get("repository")}))
