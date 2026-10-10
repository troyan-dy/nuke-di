# <a id="mcp-servers"></a>MCP-серверы

[English](../../guide/mcp.md) · **Русский** · [简体中文](../zh-CN/mcp.md) · [Español](../es/mcp.md) · [Português (Brasil)](../pt-BR/mcp.md) · [日本語](../ja/mcp.md) · [Polski](../pl/mcp.md)

← [Документация](../README.ru.md#documentation)

Инструмент MCP получает клиент по аннотации типа, так же как `Context` запроса, а во входной схеме, которую
видит LLM, клиента нет. Клиенты подключаются при старте сервера и отключаются при его остановке. Модулей два,
по одному на библиотеку: `nuke_di.mcp` для `MCPServer` из официального SDK и `nuke_di.fastmcp` для FastMCP.

## <a id="the-mcp-sdk"></a>MCP SDK

```bash
pip install "nuke-di[mcp]"
```

Нужен `mcp` 2.0 или новее: его `MCPServer` заполняет аргумент инструмента с аннотацией `Annotated[T,
Resolve(fn)]`, вызывая `fn`, и `nuke_di.mcp` передаёт клиенты именно так. У класса `FastMCP` из `mcp` 1.x
такого механизма нет, и он не поддерживается. Книжный магазин с одним инструментом:

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

MCP-хост, например Claude Desktop или IDE, запускает сервер командой `python -m bookshop.server` и общается с
ним через stdio. Так же поступает и этот скрипт с клиентом из SDK:

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

Во входной схеме есть только `genre`. Строка `INFO` — сводка старта `nuke-di` в stderr сервера: логирование
настраивает SDK.

Правила:

- **Где заполняются клиенты.** В аргументах инструментов, добавленных через `@server.tool()` или
  `server.add_tool()` после `setup(server)`, и всех резолверов, которые они используют, `Annotated[T,
  Resolve(fn)]`, на любой глубине. Все остальные аргументы достаются SDK: вход инструмента, `Context`.
  Аргумент с вашим собственным `Resolve(...)` остаётся вашим, даже если его тип — клиент.
- **Только инструменты.** У SDK нет резолверов для ресурсов и промптов, поэтому клиенты они не получают:
  читайте данные в инструменте или используйте [FastMCP](#fastmcp).
- **Какие клиенты стартуют.** При старте — клиенты всех инструментов, добавленных через `setup()`. Объект
  `Tool`, созданный заранее и переданный как `MCPServer(tools=[...])`, не учитывается.
- **Lifespan.** Клиенты подключаются до собственного `lifespan=` сервера и отключаются после него, поэтому он
  может ими пользоваться. SDK выполняет lifespan один раз на процесс в stdio, один раз на приложение в
  streamable HTTP и один раз на каждый in-memory `Client`. В транспорте SSE он выполняется на каждое
  подключение, и второе одновременное подключение не стартует: используйте streamable HTTP.
- **Функция остаётся такой, как написана.** Маркеры `Resolve` есть в её аннотациях, только пока SDK их читает,
  то есть при добавлении инструмента; тест может вызвать её со своими клиентами.

## <a id="fastmcp"></a>FastMCP

```bash
pip install "nuke-di[fastmcp]"
```

Нужен FastMCP 4.0 или новее. FastMCP заполняет аргумент, значение по умолчанию которого — `Depends(fn)`, и не
включает его в схему, одинаково для инструментов, ресурсов и промптов; `nuke_di.fastmcp` даёт такое значение
по умолчанию каждому аргументу-клиенту. Тот же книжный магазин, с зависимостью, ресурсом и промптом:

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

До остального дотягивается собственный in-memory клиент FastMCP:

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

Правила:

- **Где заполняются клиенты.** В аргументах инструментов, ресурсов и промптов, добавленных декораторами
  сервера или его `add_tool()`, `add_resource()`, `add_prompt()` после `setup(mcp)`, и всех функций в
  `Depends(...)`, которые они используют, на любой глубине. Аргумент с собственным значением по умолчанию не
  трогается, даже если его тип — клиент.
- **Сигнатура меняется.** FastMCP читает `Depends` только из значений по умолчанию, поэтому аргумент-клиент
  становится keyword-only со значением по умолчанию `Depends(...)` и идёт после аргументов, которые передаёт
  вызывающий код: `count_books(genre, *, catalog=Depends(...))`. Это видно только при интроспекции: тест
  вызывает функцию так, как она написана.
- **Lifespan.** Клиенты подключаются до собственного `lifespan=` сервера и отключаются после него. FastMCP
  выполняет lifespan один раз, сколько бы сессий или транспортов ни разделяли сервер.
- **Один сервер за раз.** FastMCP сохраняет сигнатуру функции навсегда, поэтому функция переписывается один
  раз, каким бы ни был контейнер: два сервера с общей функцией-инструментом запускаются по очереди, и второй,
  который стартует, пока работает первый, не запускается.

## <a id="testing"></a>Тестирование

Тест подменяет клиент через `override()` до того, как in-memory клиент запустит сервер:

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

С FastMCP тест такой же, только с `fastmcp.Client(mcp)` и `result.data == 1`. Replacement никогда не
подключается, поэтому книги у `FakeDatabase` берутся из `__init__`.

## <a id="errors"></a>Ошибки

- **Инструмент, добавленный до `setup()`.** SDK и FastMCP сразу читают сигнатуру инструмента, а клиент — не
  тот тип, который они знают:

  ```
  TypeError: Catalog is a nuke-di client, not a pydantic type. A pydantic model takes it only with arbitrary_types_allowed, and has no JSON schema for it. A framework fills it only as a plain type hint, not as an optional, through its nuke-di integration: e.g. in a FastAPI route declared through nuke_di.fastapi, or in an MCP tool added after setup() of nuke_di.mcp or nuke_di.fastmcp. See https://github.com/troyan-dy/nuke-di#documentation
  ```

- **Инструмент, вызванный без lifespan**, например `await server.call_tool(...)` в тесте без клиента: SDK
  выбрасывает `UnexpectedToolError: Error executing tool count_books`, а FastMCP — `ToolError: Error calling
  tool 'count_books': Failed to resolve dependency 'catalog' for count_books`, и причина у обоих —
  `RuntimeError: Catalog is not connected: run the server with its lifespan`.
- **Инструмент, добавленный после старта сервера**, выбрасывает `RuntimeError: Catalog was not started with
  the server: add its tool before the server starts` (у FastMCP: "its tool, resource or prompt").
- **Упавший `connect()`** проваливает старт: `async with Client(server)` выбрасывает `RuntimeError: nuke-di
  clients failed to start: Database.connect() raised OSError: db.local:5432 is unreachable` (клиент FastMCP
  добавляет перед этим `Client failed to connect: `), а сервер на stdio завершается с кодом 1 и этой ошибкой в
  трейсбеке, и хост сообщает о сервере, который не запустился.
- **`setup()` дважды** для одного сервера: `TypeError: setup() was already called for this app`.
- **Два сервера FastMCP с общей функцией одновременно:** второй не запускается с `RuntimeError: nuke-di
  clients failed to start: Catalog is filled for another app that is running; apps that share a handler
  function run one at a time`.
