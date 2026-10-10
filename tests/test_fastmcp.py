"""
The FastMCP integration: tools, resources and prompts of a `FastMCP` server, and the functions they depend on,
take clients by type hint, through FastMCP's `Depends`.
"""

import functools
import inspect
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastmcp import Client as MCPClient
from fastmcp import Context, FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from mcp.types import TextContent, TextResourceContents

from nuke_di import DI, Client, Dependencies
from nuke_di.fastapi import setup as fastapi_setup
from nuke_di.fastmcp import _FASTMCP, setup
from nuke_di.integration.testing import Send, check

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


def make_server(container: Dependencies, **kwargs: Any) -> FastMCP[Any]:
    server: FastMCP[Any] = FastMCP("bookshop", **kwargs)
    setup(server, container)
    return server


async def call(server: FastMCP[Any], name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call a tool through an in-memory client, which runs the server's lifespan."""
    async with MCPClient(server) as client:
        return (await client.call_tool(name, arguments or {})).data


# --- tools -----------------------------------------------------------------------------------------------


async def test_tool_gets_client_by_type_hint() -> None:
    async def count_books(genre: str, catalog: Catalog) -> int:
        return catalog.count(genre)

    server = make_server(Dependencies())
    server.tool(count_books)

    assert await call(server, "count_books", {"genre": "fantasy"}) == 2
    assert events == ["database: connected", "database: disconnected"]


async def test_client_is_left_out_of_the_schema() -> None:
    async def count_books(catalog: Catalog, genre: str, limit: int = 10) -> int:
        return min(catalog.count(genre), limit)  # pragma: no cover

    server = make_server(Dependencies())
    server.tool(count_books)

    async with MCPClient(server) as client:
        (tool,) = await client.list_tools()

    assert tool.input_schema["properties"] == {"genre": {"type": "string"}, "limit": {"default": 10, "type": "integer"}}
    assert tool.input_schema["required"] == ["genre"]
    # The client goes after the arguments the caller passes, with FastMCP's marker as its default
    parameters = inspect.signature(count_books).parameters
    assert list(parameters) == ["genre", "limit", "catalog"]
    assert parameters["catalog"].kind is inspect.Parameter.KEYWORD_ONLY


async def test_override_before_startup() -> None:
    async def count_books(genre: str, catalog: Catalog) -> int:
        return catalog.count(genre)

    container = Dependencies()
    server = make_server(container)
    server.add_tool(count_books)

    with container.override(Database, FakeDatabase()):
        assert await call(server, "count_books", {"genre": "fantasy"}) == 1


async def test_global_container_by_default() -> None:
    async def count_books(genre: str, catalog: Catalog) -> int:
        return catalog.count(genre)

    server: FastMCP[Any] = FastMCP("bookshop")
    setup(server)
    server.tool(count_books)

    with DI.override(Database, FakeDatabase()):
        assert await call(server, "count_books", {"genre": "fantasy"}) == 1


async def test_function_stays_callable_directly() -> None:
    async def count_books(genre: str, catalog: Catalog) -> int:
        return catalog.count(genre)

    make_server(Dependencies()).tool(count_books)

    assert await count_books("fantasy", Catalog(FakeDatabase())) == 1


async def test_sync_tool_with_context() -> None:
    def shelf(genre: str, ctx: Context, catalog: Catalog) -> str:
        return f"{ctx.fastmcp.name}: {', '.join(catalog.db.books[genre])}"

    server = make_server(Dependencies())
    server.tool(shelf)

    assert await call(server, "shelf", {"genre": "poetry"}) == "bookshop: Leaves of Grass"


async def test_bound_method_tool() -> None:
    class Shop:
        def __init__(self, name: str) -> None:
            self.name = name

        async def count(self, genre: str, catalog: Catalog) -> str:
            return f"{self.name}: {catalog.count(genre)}"

    server = make_server(Dependencies())
    server.add_tool(Shop("corner").count)

    assert await call(server, "count", {"genre": "fantasy"}) == "corner: 2"


async def test_tool_object_and_callable_object_are_left_to_fastmcp() -> None:
    class Hello:
        __name__ = "hello"

        def __call__(self, name: str) -> str:
            return f"Hello, {name}!"

    def bye(name: str) -> str:
        return f"Bye, {name}!"

    server = make_server(Dependencies())
    server.add_tool(Hello())
    server.add_tool(Tool.from_function(bye))

    assert await call(server, "hello", {"name": "Ada"}) == "Hello, Ada!"
    assert await call(server, "bye", {"name": "Ada"}) == "Bye, Ada!"
    assert events == []


async def test_client_with_a_default_is_filled() -> None:
    async def count_books(genre: str, catalog: Catalog = None) -> int:  # type: ignore[assignment]
        return catalog.count(genre)

    server = make_server(Dependencies())
    server.tool(count_books)

    assert await call(server, "count_books", {"genre": "fantasy"}) == 2


def test_partial_callable_object_and_class_with_clients_are_refused() -> None:
    def count(genre: str, catalog: Catalog) -> int:
        return catalog.count(genre)  # pragma: no cover

    class Counter:
        __name__ = "counter"

        def __call__(self, genre: str, catalog: Catalog) -> int:
            return catalog.count(genre)  # pragma: no cover

    class Reader:
        def __init__(self, db: Database) -> None:
            self.db = db  # pragma: no cover

    async def read(reader: Reader = Depends(Reader)) -> str:
        return ""  # pragma: no cover

    server = make_server(Dependencies())

    with pytest.raises(TypeError, match=r'Argument "catalog" of a functools\.partial of .*count is Catalog'):
        server.add_tool(functools.partial(count, "fantasy"))
    with pytest.raises(TypeError, match=r'Argument "catalog" of the callable object .*Counter is Catalog'):
        server.add_tool(Counter())
    with pytest.raises(TypeError, match=r'Argument "db" of the class .*Reader is Database: .* declare a function'):
        server.tool(read)


def test_function_read_by_fastmcp_before_setup_is_refused() -> None:
    async def count_books(genre: str, catalog: Catalog) -> int:
        return catalog.count(genre)  # pragma: no cover

    with pytest.raises(TypeError, match="not a pydantic type"):
        FastMCP("plain").tool(count_books)

    with pytest.raises(TypeError, match=r"FastMCP read .*count_books before setup\(\) and keeps the signature"):
        make_server(Dependencies()).tool(count_books)


def test_one_function_for_fastapi_and_fastmcp_is_refused() -> None:
    async def for_fastapi_first(catalog: Catalog) -> int:
        return 1  # pragma: no cover

    async def for_fastmcp_first(catalog: Catalog) -> int:
        return 1  # pragma: no cover

    app = FastAPI()
    fastapi_setup(app, Dependencies())
    app.get("/first")(for_fastapi_first)
    server = make_server(Dependencies())
    server.tool(for_fastmcp_first)

    with pytest.raises(TypeError, match="takes clients in both FastAPI and FastMCP handlers"):
        server.tool(for_fastapi_first)
    with pytest.raises(TypeError, match="takes clients in both FastMCP and FastAPI handlers"):
        app.get("/second")(for_fastmcp_first)


async def test_own_dependency_of_a_client_type_is_kept() -> None:
    def fake() -> Catalog:
        return Catalog(FakeDatabase())

    async def count_books(genre: str, catalog: Catalog = Depends(fake)) -> int:
        return catalog.count(genre)

    server = make_server(Dependencies())
    server.tool(count_books)

    assert await call(server, "count_books", {"genre": "fantasy"}) == 1
    assert events == []


async def test_server_lifespan_runs_inside_with_connected_clients() -> None:
    container = Dependencies()

    @asynccontextmanager
    async def lifespan(server: FastMCP[Any]) -> AsyncIterator[dict[str, int]]:
        events.append(f"lifespan: connected={container.connected}")
        yield {"answer": 42}
        events.append(f"lifespan: connected={container.connected}")

    async def answer(ctx: Context, catalog: Catalog) -> int:
        return int(ctx.lifespan_context["answer"])

    server = make_server(container, lifespan=lifespan)
    server.tool(answer)

    assert await call(server, "answer") == 42
    assert events == [
        "database: connected",
        "lifespan: connected=True",
        "lifespan: connected=True",
        "database: disconnected",
    ]


# --- dependencies, resources and prompts -----------------------------------------------------------------


async def test_dependency_takes_clients() -> None:
    def genres(catalog: Catalog, **options: Any) -> list[str]:
        return sorted(catalog.db.books)

    async def first_genre(db: Database, names: list[str] = Depends(genres)) -> str:
        return f"{names[0]} ({len(db.books[names[0]])})"

    async def browse(genre: str = Depends(first_genre)) -> str:
        return genre

    server = make_server(Dependencies())
    server.tool(browse)

    async with MCPClient(server) as client:
        (tool,) = await client.list_tools()
        result = await client.call_tool("browse", {})

    assert tool.input_schema["properties"] == {}
    assert result.data == "fantasy (2)"
    assert list(inspect.signature(genres).parameters) == ["catalog", "options"]


async def test_resources_and_prompts_take_clients() -> None:
    server = make_server(Dependencies())

    @server.resource("books://genres")
    async def genres(catalog: Catalog) -> str:
        return ", ".join(sorted(catalog.db.books))

    @server.resource("books://{genre}")
    async def shelf(genre: str, db: Database) -> str:
        return ", ".join(db.books[genre])

    @server.prompt
    async def recommend(genre: str, catalog: Catalog) -> str:
        return f"Recommend one of the {catalog.count(genre)} {genre} books."

    async with MCPClient(server) as client:
        (static,) = await client.read_resource("books://genres")
        (template,) = await client.read_resource("books://fantasy")
        prompt = await client.get_prompt("recommend", {"genre": "fantasy"})
        (listed,) = await client.list_prompts()

    assert isinstance(static, TextResourceContents)
    assert isinstance(template, TextResourceContents)
    assert (static.text, template.text) == ("fantasy, poetry", "The Hobbit, Earthsea")
    (message,) = prompt.messages
    assert isinstance(message.content, TextContent)
    assert message.content.text == "Recommend one of the 2 fantasy books."
    assert [argument.name for argument in listed.arguments or []] == ["genre"]


# --- mounted servers -------------------------------------------------------------------------------------


async def test_mounted_server_on_the_same_container_shares_its_clients() -> None:
    async def count_books(genre: str, catalog: Catalog) -> int:
        return catalog.count(genre)

    async def titles(genre: str, db: Database) -> list[str]:
        return db.books[genre]

    container = Dependencies()
    parent, child = make_server(container), make_server(container)
    parent.tool(count_books)
    child.tool(titles)
    parent.mount(child, namespace="shelf")

    async with MCPClient(parent) as client:
        assert (await client.call_tool("count_books", {"genre": "fantasy"})).data == 2
        assert (await client.call_tool("shelf_titles", {"genre": "poetry"})).data == ["Leaves of Grass"]
    # On its own, the mounted server starts its clients itself
    assert await call(child, "titles", {"genre": "poetry"}) == ["Leaves of Grass"]

    assert events == ["database: connected", "database: disconnected"] * 2


async def test_mounted_server_on_another_container_starts_its_own() -> None:
    async def titles(genre: str, db: Database) -> list[str]:
        return db.books[genre]

    parent, child = make_server(Dependencies()), make_server(Dependencies())
    child.tool(titles)
    parent.mount(child)

    assert await call(parent, "titles", {"genre": "poetry"}) == ["Leaves of Grass"]
    assert events == ["database: connected", "database: disconnected"]


async def test_server_mounted_before_setup_on_the_same_container_fails_to_start() -> None:
    async def titles(genre: str, db: Database) -> list[str]:
        return db.books[genre]  # pragma: no cover

    container = Dependencies()
    parent: FastMCP[Any] = FastMCP("parent")
    child = make_server(container)
    child.tool(titles)
    parent.mount(child)
    setup(parent, container)

    with pytest.raises(RuntimeError, match=r"bookshop is mounted on a server that runs on the same container"):
        async with MCPClient(parent):
            pass  # pragma: no cover
    assert not container.connected


# --- one function, two servers ---------------------------------------------------------------------------


async def count_books(genre: str, catalog: Catalog) -> int:
    return catalog.count(genre)


async def test_function_is_bound_once_whatever_the_container() -> None:
    first, second = Dependencies(), Dependencies()
    server_a, server_b = make_server(first), make_server(second)
    server_a.tool(count_books)
    server_b.tool(count_books)

    assert await call(server_a, "count_books", {"genre": "fantasy"}) == 2
    with second.override(Database, FakeDatabase()):
        assert await call(server_b, "count_books", {"genre": "fantasy"}) == 1

    # Two servers that share a function run one at a time
    async with MCPClient(server_a):
        with pytest.raises(RuntimeError, match="Catalog is filled for another app that is running"):
            async with MCPClient(server_b):
                pass  # pragma: no cover


# --- errors ----------------------------------------------------------------------------------------------


async def test_tool_called_without_the_lifespan() -> None:
    async def count_books(genre: str, catalog: Catalog) -> int:
        return catalog.count(genre)  # pragma: no cover

    server = make_server(Dependencies())
    server.tool(count_books)

    with pytest.raises(ToolError) as raised:
        await server.call_tool("count_books", {"genre": "fantasy"})

    assert str(raised.value.__cause__.__cause__) == (  # type: ignore[union-attr]
        "Catalog is not connected: run the server with its lifespan, e.g. `async with Client(server)`"
    )


async def test_tool_added_after_startup() -> None:
    async def late(catalog: Catalog) -> int:
        return catalog.count("fantasy")  # pragma: no cover

    async def count_books(genre: str, db: Database) -> int:
        return len(db.books[genre])

    server = make_server(Dependencies())
    server.tool(count_books)

    async with MCPClient(server) as client:
        server.tool(late)
        with pytest.raises(ToolError) as raised:
            await server.call_tool("late", {})
        assert (await client.call_tool("count_books", {"genre": "fantasy"})).data == 2

    assert str(raised.value.__cause__.__cause__) == (  # type: ignore[union-attr]
        "Catalog was not started with the server: add its tool, resource or prompt before the server starts"
    )


async def test_failed_connect_fails_the_startup() -> None:
    async def broken(client: Broken) -> None: ...  # pragma: no cover

    container = Dependencies()
    server = make_server(container)
    server.tool(broken)

    with pytest.raises(RuntimeError, match=r"nuke-di clients failed to start: Broken\.connect\(\) raised OSError"):
        async with MCPClient(server):
            pass  # pragma: no cover

    assert not container.connected
    assert not container.clients


def test_setup_twice_is_refused() -> None:
    server = make_server(Dependencies())

    with pytest.raises(TypeError, match="setup\\(\\) was already called"):
        setup(server, Dependencies())


def test_unresolvable_annotation_is_left_to_fastmcp() -> None:
    def tool(value: "Missing") -> int:  # type: ignore[name-defined]  # noqa: F821
        return 1  # pragma: no cover

    server = make_server(Dependencies())
    with pytest.raises(NameError, match="Missing"):
        server.add_tool(tool)


def test_tool_declared_before_setup_names_the_fix() -> None:
    async def count_books(genre: str, catalog: Catalog) -> int:
        return catalog.count(genre)  # pragma: no cover

    server: FastMCP[Any] = FastMCP("bookshop")

    with pytest.raises(TypeError, match=r"Catalog is a nuke-di client.*nuke_di\.fastmcp"):
        server.tool(count_books)


# --- the contract ----------------------------------------------------------------------------------------


def contract_server(container: Dependencies, handler: Callable[..., Any]) -> FastMCP[Any]:
    server = make_server(container)
    server.tool(handler, name="check")
    return server


@asynccontextmanager
async def contract_run(server: FastMCP[Any], lifespan: bool) -> AsyncIterator[Send]:
    if not lifespan:
        # FastMCP raises the error of a dependency as the cause of its own
        yield lambda: server.call_tool("check", {})
        return
    async with MCPClient(server) as client:
        yield lambda: client.call_tool("check", {})


async def test_contract() -> None:
    await check(_FASTMCP, contract_server, contract_run)
