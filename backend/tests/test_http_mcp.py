import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path

import httpx

from macbot.mcp_client import ServerConfig, create_server, execute_tool, fingerprint, inspect_server
from macbot.store import Store


async def test_real_streamable_http_discovery_and_call(tmp_path):
    store = Store(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    output = (tmp_path / "http-mcp.log").open("w")
    child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("http_mcp_server.py")), str(port)], stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
            for _ in range(50):
                try:
                    response = await client.get(f"http://127.0.0.1:{port}/mcp")
                    if response.status_code != 404:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise AssertionError((tmp_path / "http-mcp.log").read_text())
        public = create_server(store, ServerConfig(name="Local HTTP", transport="http", url=f"http://127.0.0.1:{port}/mcp", enabled=True, trusted=True))
        config = json.loads(store.execute("SELECT data FROM mcp_servers WHERE id=?", (public["id"],))[0]["data"])
        tools = await inspect_server(config, tmp_path)
        assert tools[0]["name"] == "echo"
        value = await execute_tool(store, "http", {"server_id": public["id"], "config_hash": fingerprint(config), "tool": "echo", "arguments": {"value": "HTTP MCP works"}})
        assert "HTTP MCP works" in value["text"]
    finally:
        child.terminate()
        child.wait(timeout=5)
        output.close()
        store.db.close()
