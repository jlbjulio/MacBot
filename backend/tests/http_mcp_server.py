import sys
from mcp.server.mcpserver import MCPServer

server = MCPServer("HTTP regression")

@server.tool()
def echo(value: str) -> str:
    return value

if __name__ == "__main__":
    server.run(transport="streamable-http", host="127.0.0.1", port=int(sys.argv[1]))
