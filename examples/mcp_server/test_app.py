import fastmcp
from mcp import Client
from mcp.types import TextResourceContents
from nuke_di import DI

from mcp_server.clients import Database
from mcp_server.fastmcp_server import mcp
from mcp_server.server import server


class FakeDatabase(Database):
    # A Replacement is never connected, so it has its books from the start
    def __init__(self) -> None:
        self.books = {"fantasy": ["Dune"]}


async def test_count_books() -> None:
    # An in-memory client runs the server's lifespan, which connects the clients
    with DI.override(Database, FakeDatabase()):
        async with Client(server) as client:
            result = await client.call_tool("count_books", {"genre": "fantasy"})

    assert result.structured_content == {"result": 1}


async def test_clients_are_not_in_the_schema() -> None:
    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    assert {tool.name: list(tool.input_schema["properties"]) for tool in tools} == {
        "count_books": ["genre"],
        "list_books": ["genre"],
    }


async def test_fastmcp_server() -> None:
    with DI.override(Database, FakeDatabase()):
        async with fastmcp.Client(mcp) as client:
            count = await client.call_tool("count_books", {"genre": "fantasy"})
            recommended = await client.call_tool("recommend", {})
            (genres,) = await client.read_resource("books://genres")

    assert (count.data, recommended.data) == (1, "Start with Dune.")
    assert isinstance(genres, TextResourceContents)
    assert genres.text == '["fantasy"]'
