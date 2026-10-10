"""
The bookshop as an MCP server on the official SDK. Run it over stdio: `python -m mcp_server.server`.
"""

from mcp.server.mcpserver import MCPServer
from nuke_di.mcp import setup

from mcp_server.clients import Catalog, Database

server = MCPServer("bookshop")
setup(server)  # before the tools; the clients connect when the server starts


@server.tool()
async def count_books(genre: str, catalog: Catalog) -> int:
    """How many books of a genre the shop has."""
    return await catalog.count(genre)


@server.tool()
async def list_books(genre: str, db: Database) -> list[str]:
    """The titles of a genre."""
    return await db.titles(genre)


if __name__ == "__main__":
    server.run()
