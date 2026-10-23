"""
The MCP SDK integration: the tools of an `MCPServer` take clients by type hint, through the SDK's `Resolve`.
"""

import functools
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import pytest
from mcp import Client as MCPClient
from mcp.server.mcpserver import Context, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import InvalidSignature, ToolError
from mcp.types import InputRequiredResult
from pydantic import BaseModel, ConfigDict
from pydantic.json_schema import GenerateJsonSchema

from nuke_di import DI, Client, Dependencies
from nuke_di.integration.testing import Send, check
from nuke_di.mcp import _MCP, setup
from tests.sequences import single

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
        tool_result = await client.call_tool(name, arguments or {})
    assert not tool_result.is_error, tool_result.content
    return tool_result.structured_content


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
        tool = single((await client.list_tools()).tools)

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
    assert "__signature__" not in count_books.__dict__
    if sys.version_info >= (3, 14):
        # The annotations as written, not only their values: `__annotate__` is kept
        import annotationlib

        assert annotationlib.get_annotations(count_books, format=annotationlib.Format.STRING) == {
            "genre": "str",
            "catalog": "Catalog",
            "return": "int",
        }
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
        has_request = ctx.request_id is not None
        count = catalog.count(genre)
        return f"{has_request}: {count}"

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


async def test_callable_object_takes_clients() -> None:
    class Counter:
        __name__ = "count"

        def __call__(self, genre: str, catalog: Catalog) -> int:
            return catalog.count(genre)

    server = make_server(Dependencies())
    server.add_tool(Counter(), name="count")

    async with MCPClient(server) as client:
        tool = single((await client.list_tools()).tools)
        tool_result = await client.call_tool("count", {"genre": "fantasy"})

    assert list(tool.input_schema["properties"]) == ["genre"]
    assert tool_result.structured_content == {"result": 2}
    assert "Annotated" not in str(Counter.__call__.__annotations__)


async def test_callable_object_is_left_to_the_sdk() -> None:
    class Hello:
        __name__ = "hello"

        def __call__(self, name: str) -> str:
            return f"Hello, {name}!"

    server = make_server(Dependencies())
    server.add_tool(Hello(), name="hello")
    # A builtin has no annotations to fill
    server.add_tool(len, name="size")

    assert await call(server, "hello", {"name": "Ada"}) == {"result": "Hello, Ada!"}
    assert events == []


async def test_client_with_a_default_is_filled() -> None:
    async def count(genre: str, catalog: Catalog = None) -> int:  # type: ignore[assignment]
        return catalog.count(genre)

    server = make_server(Dependencies())
    server.tool()(count)

    assert await call(server, "count", {"genre": "fantasy"}) == {"result": 2}


def test_partial_with_clients_is_refused() -> None:
    def count(genre: str, catalog: Catalog) -> int:
        return catalog.count(genre)

    server = make_server(Dependencies())

    with pytest.raises(TypeError, match=r'Argument "catalog" of a functools.partial of .*count is Catalog'):
        server.add_tool(functools.partial(count, "fantasy"), name="fantasy")


def test_partial_without_clients_is_left_to_the_sdk() -> None:
    def greet(greeting: str, name: str) -> str:
        return f"{greeting}, {name}!"

    def missing(title: "Missing") -> str:  # type: ignore[name-defined]  # noqa: F821
        return ""

    server = make_server(Dependencies())
    # The SDK takes no partials: it reads their `__name__`
    for partial in (functools.partial(greet, "Hello"), functools.partial(missing)):
        with pytest.raises(AttributeError, match="__name__"):
            server.add_tool(partial, name="greet")


def test_input_required_result_with_clients_is_refused() -> None:
    async def ask(catalog: Catalog) -> InputRequiredResult | int:
        return 1

    server = make_server(Dependencies())

    with pytest.raises(TypeError, match="takes clients and returns an InputRequiredResult") as raised:
        server.tool()(ask)
    assert isinstance(raised.value.__cause__, InvalidSignature)


async def test_one_function_on_two_servers_with_two_containers() -> None:
    second = Dependencies()
    server_a = make_server(Dependencies())
    server_b = make_server(second)
    server_a.tool()(count_books)
    server_b.tool()(count_books)

    with second.override(Database, FakeDatabase()):
        async with MCPClient(server_a) as client_a, MCPClient(server_b) as client_b:
            assert await fantasy_count(client_a) == {"result": 2}
            assert await fantasy_count(client_b) == {"result": 1}


async def fantasy_count(client: MCPClient) -> Any:
    tool_result = await client.call_tool("count_books", {"genre": "fantasy"})
    return tool_result.structured_content


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
        tool = single((await client.list_tools()).tools)
        tool_result = await client.call_tool("browse", {})

    assert tool.input_schema["properties"] == {}
    assert tool_result.structured_content == {"result": "fantasy (2)"}
    assert "__signature__" not in shelf.__dict__
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


async def endless(count: int) -> int:
    return count


# A resolver that resolves its own argument
endless.__annotations__["count"] = Annotated[int, Resolve(endless)]


def test_cycle_of_resolvers_is_reported_by_the_sdk() -> None:
    async def spin(count: Annotated[int, Resolve(endless)], catalog: Catalog) -> int:
        return count

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
        return catalog.count("fantasy")

    async with MCPClient(server) as client:
        server.tool()(late)
        with pytest.raises(ToolError) as raised:
            await server.call_tool("late", {})
        assert (await client.call_tool("count_books", {"genre": "fantasy"})).structured_content == {"result": 2}

    assert str(raised.value.__cause__) == (
        "Catalog was not started with the server: add its tool before the server starts"
    )


async def test_failed_connect_fails_the_startup() -> None:
    async def broken(client: Broken) -> None: ...

    container = Dependencies()
    server = make_server(container)
    server.tool()(broken)

    with pytest.raises(RuntimeError, match=r"nuke-di clients failed to start: Broken\.connect\(\) raised OSError"):
        async with MCPClient(server):
            ...

    assert not container.connected
    assert not container.clients


def test_setup_twice_is_refused() -> None:
    server = make_server(Dependencies())

    with pytest.raises(TypeError, match=r"setup\(\) was already called"):
        setup(server, Dependencies())


def test_unresolvable_annotation_is_left_to_the_sdk() -> None:
    def tool(title: "Missing") -> int:  # type: ignore[name-defined]  # noqa: F821
        return 1

    server = make_server(Dependencies())
    with pytest.raises(Exception, match="Unable to evaluate type annotations for callable 'tool'"):
        server.add_tool(tool)


def test_tolerant_json_schema_generator_keeps_working() -> None:
    # A JSON schema generator that tolerates arbitrary types gets its own answer, not the TypeError
    class Tolerant(GenerateJsonSchema):
        def handle_invalid_for_json_schema(self, schema: Any, error_info: str) -> dict[str, Any]:
            return {"title": "invalid"}

    class Model(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        catalog: Catalog

    assert Model.model_json_schema(schema_generator=Tolerant)["properties"]["catalog"] == {"title": "invalid"}
    with pytest.raises(TypeError, match="Catalog is a nuke-di client, not a pydantic type"):
        Model.model_json_schema()


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
            tool_result = await client.call_tool("check", {})
            assert not tool_result.is_error, tool_result.content

        yield send


async def test_contract() -> None:
    await check(_MCP, contract_server, contract_run)
