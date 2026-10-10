"""
The MCP SDK integration: the tools of an `MCPServer` take clients by type hint, through the SDK's `Resolve`.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import pytest
from mcp import Client as MCPClient
from mcp.server.mcpserver import Context, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import InvalidSignature, ToolError

from nuke_di import DI, Client, Dependencies
from nuke_di.integration.testing import Send, check
from nuke_di.mcp import _MCP, setup

events: list[str] = []


@pytest.fixture(autouse=True)
def clear_events() -> Iterator[None]:
    events.clear()
    yield
    DI.flush()


class Database(Client):
    async def connect(self) -> None:
        events.append("database: connected")
        self.books = {"fantasy": ["The Hobbit", "Earthsea"], "poetry": ["Leaves of Grass"]}

    async def disconnect(self) -> None:
        events.append("database: disconnected")


class Catalog(Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    def count(self, genre: str) -> int:
        return len(self.db.books.get(genre, []))


class FakeDatabase(Database):
    def __init__(self) -> None:
        self.books = {"fantasy": ["Dune"]}


class Broken(Client):
    async def connect(self) -> None:
        raise OSError("unreachable")


def make_server(container: Dependencies, **kwargs: Any) -> MCPServer:
    server = MCPServer("bookshop", **kwargs)
    setup(server, container)
    return server


async def call(server: MCPServer, name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call a tool through an in-memory client, which runs the server's lifespan."""
    async with MCPClient(server) as client:
        result = await client.call_tool(name, arguments or {})
    assert not result.is_error, result.content
    return result.structured_content


async def count_books(genre: str, catalog: Catalog) -> int:
    """How many books of a genre the shop has."""
    return catalog.count(genre)


# --- tools -----------------------------------------------------------------------------------------------


async def test_tool_gets_client_by_type_hint() -> None:
    server = make_server(Dependencies())
    server.tool()(count_books)

    assert await call(server, "count_books", {"genre": "fantasy"}) == {"result": 2}
    assert events == ["database: connected", "database: disconnected"]


async def test_client_is_left_out_of_the_schema() -> None:
    server = make_server(Dependencies())
    server.tool()(count_books)

    async with MCPClient(server) as client:
        (tool,) = (await client.list_tools()).tools

    assert tool.input_schema["properties"] == {"genre": {"title": "Genre", "type": "string"}}
    assert tool.input_schema["required"] == ["genre"]


async def test_override_before_startup() -> None:
    container = Dependencies()
    server = make_server(container)
    server.add_tool(count_books)

    with container.override(Database, FakeDatabase()):
        assert await call(server, "count_books", {"genre": "fantasy"}) == {"result": 1}


async def test_global_container_by_default() -> None:
    server = MCPServer("bookshop")
    setup(server)
    server.tool()(count_books)

    with DI.override(Database, FakeDatabase()):
        assert await call(server, "count_books", {"genre": "fantasy"}) == {"result": 1}


async def test_function_is_left_as_written() -> None:
    server = make_server(Dependencies())
    annotations = dict(count_books.__annotations__)
    server.tool()(count_books)

    assert count_books.__annotations__ == annotations
    assert "__signature__" not in vars(count_books)
    # A test can call it with a client of its own
    assert await count_books("fantasy", Catalog(FakeDatabase())) == 1


async def test_sync_tool_and_string_annotations() -> None:
    def shelf(genre: str, catalog: "Catalog") -> list[str]:
        return catalog.db.books[genre]

    server = make_server(Dependencies())
    server.tool()(shelf)

    assert await call(server, "shelf", {"genre": "poetry"}) == {"result": ["Leaves of Grass"]}


async def test_tool_takes_context_and_clients() -> None:
    async def report(genre: str, ctx: Context, catalog: Catalog) -> str:
        return f"{ctx.request_id is not None}: {catalog.count(genre)}"

    server = make_server(Dependencies())
    server.tool()(report)

    assert await call(server, "report", {"genre": "fantasy"}) == {"result": "True: 2"}


async def test_bound_method_tool() -> None:
    class Shop:
        def __init__(self, name: str) -> None:
            self.name = name

        async def count(self, genre: str, catalog: Catalog) -> str:
            return f"{self.name}: {catalog.count(genre)}"

    server = make_server(Dependencies())
    server.add_tool(Shop("corner").count, name="count")

    assert await call(server, "count", {"genre": "fantasy"}) == {"result": "corner: 2"}


async def test_callable_object_is_left_to_the_sdk() -> None:
    class Hello:
        __name__ = "hello"

        def __call__(self, name: str) -> str:
            return f"Hello, {name}!"

    server = make_server(Dependencies())
    server.add_tool(Hello(), name="hello")

    assert await call(server, "hello", {"name": "Ada"}) == {"result": "Hello, Ada!"}
    assert events == []


async def test_one_function_on_two_servers_with_two_containers() -> None:
    first, second = Dependencies(), Dependencies()
    server_a = make_server(first)
    server_b = make_server(second)
    server_a.tool()(count_books)
    server_b.tool()(count_books)

    with second.override(Database, FakeDatabase()):
        async with MCPClient(server_a) as a, MCPClient(server_b) as b:
            result_a = await a.call_tool("count_books", {"genre": "fantasy"})
            result_b = await b.call_tool("count_books", {"genre": "fantasy"})

    assert (result_a.structured_content, result_b.structured_content) == ({"result": 2}, {"result": 1})


async def test_server_lifespan_runs_inside_with_connected_clients() -> None:
    container = Dependencies()

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[dict[str, int]]:
        events.append(f"lifespan: connected={container.connected}")
        yield {"answer": 42}
        events.append(f"lifespan: connected={container.connected}")

    async def answer(ctx: Context, catalog: Catalog) -> int:
        return int(ctx.request_context.lifespan_context["answer"])

    server = make_server(container, lifespan=lifespan)
    server.tool()(answer)

    assert await call(server, "answer") == {"result": 42}
    assert events == [
        "database: connected",
        "lifespan: connected=True",
        "lifespan: connected=True",
        "database: disconnected",
    ]


# --- resolvers -------------------------------------------------------------------------------------------


async def test_resolver_takes_clients() -> None:
    async def shelf(catalog: Catalog) -> list[str]:
        return sorted(catalog.db.books)

    async def first_genre(genres: Annotated[list[str], Resolve(shelf)], db: Database) -> str:
        return f"{genres[0]} ({len(db.books[genres[0]])})"

    async def browse(genre: Annotated[str, Resolve(first_genre)]) -> str:
        return genre

    server = make_server(Dependencies())
    server.tool()(browse)

    async with MCPClient(server) as client:
        (tool,) = (await client.list_tools()).tools
        result = await client.call_tool("browse", {})

    assert tool.input_schema["properties"] == {}
    assert result.structured_content == {"result": "fantasy (2)"}
    assert "__signature__" not in vars(shelf)
    assert shelf.__annotations__ == {"catalog": Catalog, "return": list[str]}


async def test_own_resolver_of_a_client_type_is_kept() -> None:
    async def fake() -> Catalog:
        return Catalog(FakeDatabase())

    async def count(genre: str, catalog: Annotated[Catalog, Resolve(fake)]) -> int:
        return catalog.count(genre)

    server = make_server(Dependencies())
    server.tool()(count)

    assert await call(server, "count", {"genre": "fantasy"}) == {"result": 1}
    # The container never saw the client
    assert events == []


async def endless(value: int) -> int:
    return value  # pragma: no cover


# A resolver that resolves its own argument
endless.__annotations__["value"] = Annotated[int, Resolve(endless)]


def test_cycle_of_resolvers_is_reported_by_the_sdk() -> None:
    async def spin(value: Annotated[int, Resolve(endless)], catalog: Catalog) -> int:
        return value  # pragma: no cover

    server = make_server(Dependencies())
    with pytest.raises(InvalidSignature, match="cyclic dependency"):
        server.tool()(spin)
    assert spin.__annotations__["catalog"] is Catalog


# --- errors ----------------------------------------------------------------------------------------------


async def test_tool_called_without_the_lifespan() -> None:
    server = make_server(Dependencies())
    server.tool()(count_books)

    with pytest.raises(ToolError) as raised:
        await server.call_tool("count_books", {"genre": "fantasy"})

    assert str(raised.value.__cause__) == (
        "Catalog is not connected: run the server with its lifespan, e.g. `async with Client(server)`"
    )


async def test_tool_added_after_startup() -> None:
    server = make_server(Dependencies())
    server.tool()(count_books)

    async def late(catalog: Catalog) -> int:
        return catalog.count("fantasy")  # pragma: no cover

    async with MCPClient(server) as client:
        server.tool()(late)
        with pytest.raises(ToolError) as raised:
            await server.call_tool("late", {})
        assert (await client.call_tool("count_books", {"genre": "fantasy"})).structured_content == {"result": 2}

    assert str(raised.value.__cause__) == (
        "Catalog was not started with the server: add its tool before the server starts"
    )


async def test_failed_connect_fails_the_startup() -> None:
    async def broken(client: Broken) -> None: ...  # pragma: no cover

    container = Dependencies()
    server = make_server(container)
    server.tool()(broken)

    with pytest.raises(RuntimeError, match=r"nuke-di clients failed to start: Broken\.connect\(\) raised OSError"):
        async with MCPClient(server):
            pass  # pragma: no cover

    assert not container.connected
    assert not container.clients


def test_setup_twice_is_refused() -> None:
    server = make_server(Dependencies())

    with pytest.raises(TypeError, match="setup\\(\\) was already called"):
        setup(server, Dependencies())


def test_unresolvable_annotation_is_left_to_the_sdk() -> None:
    def tool(value: "Missing") -> int:  # type: ignore[name-defined]  # noqa: F821
        return 1  # pragma: no cover

    server = make_server(Dependencies())
    with pytest.raises(Exception, match="Unable to evaluate type annotations for callable 'tool'"):
        server.add_tool(tool)


def test_tool_declared_before_setup_names_the_fix() -> None:
    server = MCPServer("bookshop")

    with pytest.raises(TypeError, match=r"Catalog is a nuke-di client.*in an MCP tool added after setup\(\)"):
        server.tool()(count_books)


# --- the contract ----------------------------------------------------------------------------------------


def contract_server(container: Dependencies, handler: Callable[..., Any]) -> MCPServer:
    server = make_server(container)
    server.add_tool(handler, name="check")
    return server


@asynccontextmanager
async def contract_run(server: MCPServer, lifespan: bool) -> AsyncIterator[Send]:
    if not lifespan:
        # The SDK raises the error of the tool as the cause of its own
        yield lambda: server.call_tool("check", {})
        return
    async with MCPClient(server) as client:

        async def send() -> None:
            result = await client.call_tool("check", {})
            assert not result.is_error, result.content

        yield send


async def test_contract() -> None:
    await check(_MCP, contract_server, contract_run)
