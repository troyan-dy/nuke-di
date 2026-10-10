"""
The same bookshop on FastMCP, with a resource, a prompt and a dependency. Run it over stdio:
`python -m mcp_server.fastmcp_server`.
"""

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from nuke_di.fastmcp import setup

from mcp_server.clients import Catalog, Database

mcp = FastMCP("bookshop")
setup(mcp)  # before the tools; the clients connect when the server starts


async def bestseller(db: Database) -> str:
    """A dependency of a tool, which takes a client too."""
    return (await db.titles("fantasy"))[0]


@mcp.tool
async def count_books(genre: str, catalog: Catalog) -> int:
    """How many books of a genre the shop has."""
    return await catalog.count(genre)


@mcp.tool
async def recommend(title: str = Depends(bestseller)) -> str:
    """The book to start with."""
    return f"Start with {title}."


@mcp.resource("books://genres")
async def genres(catalog: Catalog) -> list[str]:
    return await catalog.genres()


@mcp.prompt
async def review(genre: str, catalog: Catalog) -> str:
    return f"Write a short review of our {await catalog.count(genre)} {genre} books."


if __name__ == "__main__":
    mcp.run(show_banner=False)
