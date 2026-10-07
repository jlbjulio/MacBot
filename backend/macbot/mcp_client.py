import asyncio
import hashlib
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator
from .secrets import open_config, seal_config


class ServerConfig(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    transport: str = "stdio"
    command: str = Field(default="", max_length=500)
    args: list[str] = Field(default_factory=list, max_length=30)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = Field(default="", max_length=1000)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = False
    trusted: bool = False
    cwd: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_transport(self):
        if self.transport not in ("stdio", "http", "workspace"):
            raise ValueError("Choose stdio, HTTP, or the built-in workspace.")
        if self.transport == "stdio" and not self.command:
            raise ValueError("A stdio server needs an executable command.")
        if self.transport == "http":
            from urllib.parse import urlparse
            parsed = urlparse(self.url)
            if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or not parsed.hostname:
                raise ValueError("Use a valid MCP HTTP endpoint without credentials in its URL.")
            if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Remote MCP services need HTTPS. HTTP is supported for a local loopback server.")
        if self.enabled and self.transport != "workspace" and not self.trusted:
            raise ValueError("Review and trust this MCP server before enabling it.")
        if sum(len(k) + len(v) for k, v in [*self.env.items(), *self.headers.items()]) > 16000:
            raise ValueError("The server configuration is too large.")
        return self


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def public_config(identifier, config):
    return {**config, "id": identifier, "env": {key: "••••••" for key in config.get("env", {})},
            "headers": {key: "••••••" for key in config.get("headers", {})}}


@asynccontextmanager
async def session(config, directory):
    if config["transport"] != "workspace" and not config.get("trusted"):
        raise ValueError("Review and trust this MCP server before connecting.")
    config = open_config(config)
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamable_http_client
    if config["transport"] in ("stdio", "workspace"):
        if config["transport"] == "workspace":
            command = sys.executable
            args = (["--mcp-workspace", str(directory / "workspace")] if getattr(sys, "frozen", False)
                    else [str(Path(__file__).resolve().parents[1] / "desktop_entry.py"), "--mcp-workspace", str(directory / "workspace")])
        else:
            command, args = config["command"], config["args"]
            if command.lower() in ("python", "python.exe"):
                command = sys.executable
            if command.lower() in ("npx", "npx.cmd", "npm", "npm.cmd", "node", "node.exe"):
                node = Path(sys.executable).resolve().parents[1] / "node/node.exe"
                if node.exists():
                    if command.lower().startswith(("npx", "npm")):
                        program = "npx-cli.js" if command.lower().startswith("npx") else "npm-cli.js"
                        args = [str(node.parent / "node_modules/npm/bin" / program), *args]
                    command = str(node)
        inherited = {key: value for key, value in os.environ.items()
                     if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"}}
        node_directory = Path(sys.executable).resolve().parents[1] / "node"
        inherited["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), str(node_directory), inherited.get("PATH", "")])
        inherited["PYTHONNOUSERSITE"] = "1"
        inherited["NPM_CONFIG_CACHE"] = str(directory / "cache/npm")
        parameters = StdioServerParameters(command=command, args=args, env={**inherited, **config["env"]}, cwd=config.get("cwd") or str(directory / "workspace"))
        with open(os.devnull, "w", encoding="utf-8") as log:
            async with stdio_client(parameters, errlog=log) as streams:
                async with ClientSession(*streams) as connection:
                    await connection.initialize()
                    yield connection
    else:
        import httpx2
        async with httpx2.AsyncClient(headers=config["headers"], timeout=20, follow_redirects=False, trust_env=False) as client:
            async with streamable_http_client(config["url"], http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as connection:
                    await connection.initialize()
                    yield connection


async def inspect_server(config, directory):
    async def inspect():
        async with session(config, directory) as connection:
            result = await connection.list_tools()
            return [{"name": tool.name, "description": (tool.description or "")[:500],
                     "inputSchema": tool.input_schema} for tool in result.tools[:30]]
    first_download = config.get("transport") == "stdio" and config.get("command", "").lower() in ("npx", "npx.cmd", "npm", "npm.cmd")
    return await asyncio.wait_for(inspect(), timeout=120 if first_download else 25)


async def inventory(store):
    tools = []
    for row in store.execute("SELECT * FROM mcp_servers"):
        config = json.loads(row["data"])
        if not config["enabled"] or config["transport"] != "workspace" and not config.get("trusted"):
            continue
        for tool in await inspect_server(config, store.directory):
            tools.append({**tool, "server_id": row["id"], "server_name": config["name"],
                          "config_hash": fingerprint(config)})
    return tools[:30]


async def execute_tool(store, job_id, proposal):
    import jsonschema
    rows = store.execute("SELECT data FROM mcp_servers WHERE id=?", (proposal["server_id"],))
    if not rows:
        raise ValueError("This MCP connection was removed.")
    config = json.loads(rows[0]["data"])
    if not config["enabled"] or fingerprint(config) != proposal["config_hash"]:
        raise ValueError("The connection changed. Request and approve a new tool call.")
    call_id = hashlib.sha256((job_id + json.dumps(proposal, sort_keys=True)).encode()).hexdigest()
    previous = store.execute("SELECT * FROM mcp_calls WHERE id=?", (call_id,))
    if previous:
        if previous[0]["status"] == "completed":
            return json.loads(previous[0]["result"])
        raise ValueError("A previous attempt may have executed this tool. Inspect the target before requesting it again.")
    async def call():
        async with session(config, store.directory) as connection:
            listed = await connection.list_tools()
            tool = next((item for item in listed.tools if item.name == proposal["tool"]), None)
            if tool is None:
                raise ValueError("The server no longer exposes this tool.")
            jsonschema.validate(proposal["arguments"], tool.input_schema)
            store.execute("INSERT INTO mcp_calls VALUES(?,?,?,?)", (call_id, job_id, "executing", "{}"))
            result = await connection.call_tool(tool.name, arguments=proposal["arguments"])
            text = "\n".join(item.text for item in result.content if item.type == "text")[:12000]
            if result.is_error:
                raise ValueError("The MCP server returned a tool error: " + text[:500])
            value = {"text": text, "server": config["name"], "tool": tool.name}
            store.execute("UPDATE mcp_calls SET status='completed',result=? WHERE id=?", (json.dumps(value), call_id))
            return value
    try:
        return await asyncio.wait_for(call(), timeout=45)
    except ExceptionGroup as error:
        leaf = error
        while isinstance(leaf, ExceptionGroup):
            leaf = leaf.exceptions[0]
        raise leaf from error


def add_workspace(store):
    if not store.execute("SELECT id FROM mcp_servers WHERE id='workspace'"):
        value = ServerConfig(name="MacBot workspace", transport="workspace").model_dump()
        store.execute("INSERT INTO mcp_servers VALUES(?,?)", ("workspace", json.dumps(value)))
    (store.directory / "workspace").mkdir(exist_ok=True)


def create_server(store, value):
    identifier = uuid4().hex
    store.execute("INSERT INTO mcp_servers VALUES(?,?)", (identifier, json.dumps(seal_config(value.model_dump()))))
    return public_config(identifier, value.model_dump())
