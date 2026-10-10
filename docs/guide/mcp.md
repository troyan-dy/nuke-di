# MCP servers

**English** · [Русский](../i18n/ru/mcp.md) · [简体中文](../i18n/zh-CN/mcp.md) · [Español](../i18n/es/mcp.md) · [Português (Brasil)](../i18n/pt-BR/mcp.md) · [日本語](../i18n/ja/mcp.md) · [Polski](../i18n/pl/mcp.md)

← [Documentation](../../README.md#documentation)

An MCP tool takes a client by its type hint, the way it takes the request `Context`, and the client is left out
of the input schema the LLM sees. The clients connect when the server starts and disconnect when it stops. Two
modules, one per library: `nuke_di.mcp` for the official SDK's `MCPServer`, `nuke_di.fastmcp` for FastMCP.

## The MCP SDK

```bash
pip install "nuke-di[mcp]"
```

Requires `mcp` 2.0 or newer, whose `MCPServer` fills a tool argument annotated `Annotated[T, Resolve(fn)]` by
calling `fn`; `nuke_di.mcp` hands it the clients that way. The `FastMCP` class of `mcp` 1.x has no such
mechanism and is not supported. A bookshop with one tool:

```python
# bookshop/clients.py
import sys

from nuke_di import Client


class Database(Client):
    async def connect(self) -> None:
        # A server on stdio speaks MCP on stdout, so everything else goes to stderr
        print("database: connected", file=sys.stderr)
        self.books = {"fantasy": ["A Wizard of Earthsea", "The Hobbit"], "poetry": ["Leaves of Grass"]}

    async def disconnect(self) -> None:
        print("database: disconnected", file=sys.stderr)


class Catalog(Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    def count(self, genre: str) -> int:
        return len(self.db.books.get(genre, []))
```

```python
# bookshop/server.py
from mcp.server.mcpserver import MCPServer

from bookshop.clients import Catalog
from nuke_di.mcp import setup

server = MCPServer("bookshop")
setup(server)  # before the tools: clients connect when the server starts, disconnect when it stops


@server.tool()
async def count_books(genre: str, catalog: Catalog) -> int:
    """How many books of a genre the shop has."""
    return catalog.count(genre)


if __name__ == "__main__":
    server.run()  # stdio
```

An MCP host such as Claude Desktop or an IDE starts the server with `python -m bookshop.server` and talks to
it over stdio. So does this script, with the SDK's client:

```python
# ask.py: starts the server over stdio, as an MCP host does, and calls its tool
import asyncio
import sys

from mcp import Client, StdioServerParameters


async def main() -> None:
    server = StdioServerParameters(command=sys.executable, args=["-m", sys.argv[1]])
    async with Client(server) as client:
        for tool in (await client.list_tools()).tools:
            print(tool.name, tool.input_schema["properties"])
        result = await client.call_tool("count_books", {"genre": "fantasy"})
        print(result.structured_content)


asyncio.run(main())
```

```console
$ python ask.py bookshop.server
database: connected
[10/10/26 18:46:46] INFO     Connected 2 clients in 0.00s (slowest:  core.py:177
                             Database 0.00s, Catalog 0.00s)
count_books {'genre': {'title': 'Genre', 'type': 'string'}}
{'result': 2}
database: disconnected
```

The input schema has `genre` only. The `INFO` line is the startup summary of `nuke-di` on the server's
stderr: the SDK configures logging.

The rules:

- **Where clients are filled.** In the arguments of the tools added with `@server.tool()` or
  `server.add_tool()` after `setup(server)`, and of every resolver they use, `Annotated[T, Resolve(fn)]`, at
  any depth. Every other argument is the SDK's: the tool's input, `Context`. An argument with a `Resolve(...)`
  of your own is yours, even if its type is a client.
- **Tools only.** The SDK has no resolvers for resources and prompts, so they take no clients: read the data
  in a tool, or use [FastMCP](#fastmcp).
- **Which clients start.** On startup, those of every tool added through `setup()`. A `Tool` object built
  before and passed as `MCPServer(tools=[...])` is not seen.
- **Lifespan.** The clients connect before the server's own `lifespan=` and disconnect after it, so it can use
  them. The SDK runs the lifespan once per process on stdio, once per app on streamable HTTP, once per
  in-memory `Client`. On the SSE transport it runs it for every connection, and a second connection at once
  fails to start: serve over streamable HTTP.
- **The function stays as written.** The `Resolve` markers are in its annotations only while the SDK reads
  them, when the tool is added; a test can call it with clients of its own.

## FastMCP

```bash
pip install "nuke-di[fastmcp]"
```

Requires FastMCP 4.0 or newer. FastMCP fills an argument whose default is `Depends(fn)` and leaves it out of
the schema, for tools, resources and prompts alike; `nuke_di.fastmcp` gives every client argument such a
default. The same bookshop, with a dependency, a resource and a prompt:

```python
# bookshop/fastmcp_server.py
from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from bookshop.clients import Catalog, Database
from nuke_di.fastmcp import setup

mcp = FastMCP("bookshop")
setup(mcp)  # before the tools: clients connect when the server starts, disconnect when it stops


async def bestseller(db: Database) -> str:
    return db.books["fantasy"][0]


@mcp.tool
async def count_books(genre: str, catalog: Catalog) -> int:
    """How many books of a genre the shop has."""
    return catalog.count(genre)


@mcp.tool
async def recommend(title: str = Depends(bestseller)) -> str:
    """The book to start with."""
    return f"Start with {title}."


@mcp.resource("books://{genre}")
async def shelf(genre: str, db: Database) -> list[str]:
    return db.books[genre]


@mcp.prompt
async def review(genre: str, catalog: Catalog) -> str:
    return f"Write a short review of our {catalog.count(genre)} {genre} books."


if __name__ == "__main__":
    mcp.run(show_banner=False)  # stdio
```

```console
$ python ask.py bookshop.fastmcp_server
database: connected
[10/10/26 18:46:47] INFO     Starting MCP server 'bookshop'     transport.py:241
                             with transport 'stdio'
count_books {'genre': {'type': 'string'}}
recommend {}
{'result': 2}
database: disconnected
```

FastMCP's own in-memory client reaches the rest:

```python
# browse.py: the tool with a dependency, the resource and the prompt, through FastMCP's in-memory client
import asyncio

from fastmcp import Client

from bookshop.fastmcp_server import mcp


async def main() -> None:
    async with Client(mcp) as client:
        print((await client.call_tool("recommend", {})).data)
        (shelf,) = await client.read_resource("books://poetry")
        print(shelf.text)
        (message,) = (await client.get_prompt("review", {"genre": "fantasy"})).messages
        print(message.content.text)


asyncio.run(main())
```

```console
$ python browse.py
database: connected
Start with A Wizard of Earthsea.
["Leaves of Grass"]
Write a short review of our 2 fantasy books.
database: disconnected
```

The rules:

- **Where clients are filled.** In the arguments of the tools, resources and prompts added with the
  decorators of the server or its `add_tool()`, `add_resource()`, `add_prompt()` after `setup(mcp)`, and of
  every function in a `Depends(...)` they use, at any depth. An argument with a default of its own is left
  alone, even if its type is a client.
- **The signature changes.** FastMCP reads `Depends` from defaults only, so a client argument becomes
  keyword-only with `Depends(...)` as its default, after the arguments the caller passes:
  `count_books(genre, *, catalog=Depends(...))`. Only introspection sees it: a test calls the function as it
  was written.
- **Lifespan.** The clients connect before the server's own `lifespan=` and disconnect after it. FastMCP runs
  the lifespan once however many sessions or transports share the server.
- **One server at a time.** FastMCP keeps the signature of a function for good, so a function is rewritten
  once, whatever the container: two servers that share a tool function run one after another, and the second
  to start while the first runs fails to start.

## Testing

A test replaces a client with `override()` before the in-memory client starts the server:

```python
# tests/test_server.py
from mcp import Client

from bookshop.clients import Database
from bookshop.server import server
from nuke_di import DI


class FakeDatabase(Database):
    def __init__(self) -> None:
        self.books = {"fantasy": ["Dune"]}


async def test_count_books() -> None:
    # An in-memory client runs the server's lifespan, which resolves and connects the clients
    with DI.override(Database, FakeDatabase()):
        async with Client(server) as client:
            result = await client.call_tool("count_books", {"genre": "fantasy"})

    assert result.structured_content == {"result": 1}
```

```console
$ pytest -q tests/test_server.py
.                                                                        [100%]
1 passed in 0.36s
```

With FastMCP the test is the same with `fastmcp.Client(mcp)`, and `result.data == 1`. A Replacement is never
connected, which is why `FakeDatabase` has its books from `__init__`.

## Errors

- **A tool added before `setup()`.** The SDK and FastMCP read the tool's signature at once, and the client
  is not a type they know:

  ```
  TypeError: Catalog is a nuke-di client, not a pydantic type. A pydantic model takes it only with arbitrary_types_allowed, and has no JSON schema for it. A framework fills it only as a plain type hint, not as an optional, through its nuke-di integration: e.g. in a FastAPI route declared through nuke_di.fastapi, or in an MCP tool added after setup() of nuke_di.mcp or nuke_di.fastmcp. See https://github.com/troyan-dy/nuke-di#documentation
  ```

- **A tool called without the lifespan**, e.g. `await server.call_tool(...)` in a test, without a client:
  the SDK raises `UnexpectedToolError: Error executing tool count_books`, and FastMCP `ToolError: Error calling tool
  'count_books': Failed to resolve dependency 'catalog' for count_books`, both caused by
  `RuntimeError: Catalog is not connected: run the server with its lifespan`.
- **A tool added after the server started** raises `RuntimeError: Catalog was not started with the server:
  add its tool before the server starts` (FastMCP: "its tool, resource or prompt").
- **A failed `connect()`** fails the startup: `async with Client(server)` raises `RuntimeError: nuke-di
  clients failed to start: Database.connect() raised OSError: db.local:5432 is unreachable` (FastMCP's client
  prefixes it with `Client failed to connect: `), and a server on stdio exits with code 1 and that error in
  its traceback, which the host reports as a server that failed to start.
- **`setup()` twice** for one server: `TypeError: setup() was already called for this app`.
- **Two FastMCP servers that share a function, at once:** the second fails to start with `RuntimeError:
  nuke-di clients failed to start: Catalog is filled for another app that is running; apps that share a
  handler function run one at a time`.
