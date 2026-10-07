from pathlib import Path


def run(directory):
    from mcp.server.mcpserver import MCPServer
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    server = MCPServer("MacBot workspace")

    def target(name):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError("Choose a file inside the MacBot workspace.")
        return path

    @server.tool()
    def list_files() -> list[str]:
        """List up to 100 files inside the dedicated MacBot workspace."""
        return [str(item.relative_to(root)) for item in root.rglob("*") if item.is_file()][:100]

    @server.tool()
    def read_file(name: str) -> str:
        """Read a UTF-8 text file inside the MacBot workspace, up to 100 KB."""
        path = target(name)
        if path.stat().st_size > 100000:
            raise ValueError("This text file is larger than 100 KB.")
        return path.read_text(encoding="utf-8")

    @server.tool()
    def write_file(name: str, content: str) -> str:
        """Create a new UTF-8 text file in the MacBot workspace. Existing files are preserved."""
        path = target(name)
        if len(content.encode()) > 100000:
            raise ValueError("Content is limited to 100 KB.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            output.write(content)
        return f"Created {name}"

    server.run(transport="stdio")
