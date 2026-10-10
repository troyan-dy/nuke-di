# <a id="mcp-servers"></a>MCP 服务器

[English](../../guide/mcp.md) · [Русский](../ru/mcp.md) · **简体中文** · [Español](../es/mcp.md) · [Português (Brasil)](../pt-BR/mcp.md) · [日本語](../ja/mcp.md) · [Polski](../pl/mcp.md)

← [文档](../README.zh-CN.md#documentation)

MCP 工具通过类型提示接收客户端，就像它接收请求的 `Context` 一样；客户端不会出现在 LLM 看到的输入 schema 中。
客户端在服务器启动时连接，在服务器停止时断开。每个库对应一个模块：`nuke_di.mcp` 用于官方 SDK 的 `MCPServer`，
`nuke_di.fastmcp` 用于 FastMCP。

## <a id="the-mcp-sdk"></a>MCP SDK

```bash
pip install "nuke-di[mcp]"
```

需要 `mcp` 2.0 或更高版本：它的 `MCPServer` 会调用 `fn` 来填充标注为 `Annotated[T, Resolve(fn)]` 的工具参数，
`nuke_di.mcp` 正是通过这种方式把客户端交给它。`mcp` 1.x 的 `FastMCP` 类没有这样的机制，因此不受支持。
一个只有一个工具的书店：

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

Claude Desktop 或 IDE 这样的 MCP 宿主用 `python -m bookshop.server` 启动服务器，并通过 stdio 与它通信。
下面这个脚本借助 SDK 的客户端做的也是同样的事：

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

输入 schema 中只有 `genre`。`INFO` 那一行是 `nuke-di` 在服务器 stderr 上输出的启动摘要：日志由 SDK 配置。

规则如下：

- **在哪里填充客户端。** 在 `setup(server)` 之后用 `@server.tool()` 或 `server.add_tool()` 添加的工具的参数中，
  以及它们用到的每个解析器 `Annotated[T, Resolve(fn)]` 的参数中，不论嵌套多深：函数、绑定方法和可调用对象都可以。
  带有普通默认值的参数，如 `catalog: Catalog = None`，同样会被填充。其余参数都交给 SDK 处理：
  工具的输入、`Context`。带有你自己的 `Resolve(...)` 的参数归你处理，即使它的类型是客户端。
- **只限工具。** SDK 没有为资源和提示词提供解析器，因此它们不接收客户端：请在工具中读取数据，
  或者使用 [FastMCP](#fastmcp)。`functools.partial`，以及返回 `InputRequiredResult` 的工具（SDK
  不会把它与解析器结合使用）也不接收客户端：二者在添加时都会抛出 `TypeError`。
- **哪些客户端会启动。** 启动时，通过 `setup()` 添加的每个工具的客户端都会启动。事先构建好、
  以 `MCPServer(tools=[...])` 传入的 `Tool` 对象不会被看到。
- **lifespan。** 客户端在服务器自己的 `lifespan=` 之前连接、在它之后断开，因此 lifespan 可以使用它们。
  SDK 在 stdio 上每个进程运行一次 lifespan，在 streamable HTTP 上每个应用运行一次，每个内存中的 `Client`
  运行一次。在 SSE 传输上它为每个连接都运行一次，同时到来的第二个连接会启动失败：请改用 streamable HTTP 提供服务。
  同时打开两个内存中的 `Client(server)` 也会以同样的方式失败。
- **函数保持原样。** `Resolve` 标记只在 SDK 读取注解时（即添加工具时）存在于函数的注解中；
  测试可以用自己的客户端调用它。

## <a id="fastmcp"></a>FastMCP

```bash
pip install "nuke-di[fastmcp]"
```

需要 FastMCP 4.0 或更高版本。对于默认值为 `Depends(fn)` 的参数，FastMCP 会填充它并把它排除在 schema 之外，
工具、资源和提示词都是如此；`nuke_di.fastmcp` 为每个客户端参数都设置这样的默认值。
同一个书店，加上一个依赖、一个资源和一个提示词：

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

FastMCP 自己的内存客户端可以访问其余部分：

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

规则如下：

- **在哪里填充客户端。** 在 `setup(mcp)` 之后用服务器的装饰器或其 `add_tool()`、`add_resource()`、
  `add_prompt()` 以函数或绑定方法形式添加的工具、资源和提示词的参数中，以及它们用到的每个 `Depends(...)`
  中函数的参数中，不论嵌套多深。带有普通默认值的参数，如 `catalog: Catalog = None`，同样会被填充；
  带有 `Depends(...)` 或 FastMCP 其他标记的参数不会被处理，即使它的类型是客户端。事先构建好的 `Tool` 对象，
  如 `mcp.add_tool(Tool.from_function(fn))`，不会被看到。接收客户端的 `functools.partial`、可调用对象或
  `Depends(...)` 中的类会抛出 `TypeError`：请声明一个函数。
- **签名会改变。** FastMCP 只从默认值中读取 `Depends`，因此客户端参数会变成仅限关键字参数，
  以 `Depends(...)` 为默认值，排在调用方传入的参数之后：`count_books(genre, *, catalog=Depends(...))`。
  只有内省才能看到这一点：测试按函数原本的写法调用它。
- **lifespan。** 客户端在服务器自己的 `lifespan=` 之前连接、在它之后断开。无论有多少会话或传输共享该服务器，
  FastMCP 都只运行一次 lifespan。
- **一次一个服务器。** FastMCP 会永久保留函数的签名，因此函数只会被重写一次，与容器无关：
  共享同一个工具函数的两个服务器要依次运行，当第一个正在运行时，第二个会启动失败。出于同样的原因，
  一个函数只能服务于 FastMCP 或 FastAPI 之一，不能同时服务两者。
- **挂载的服务器。** 在每个函数接收客户端的服务器上调用 `setup()`，然后再调用 `mount()`。
  挂载到使用同一容器的服务器上的服务器由后者启动：两者的客户端只连接一次，即在你运行的服务器启动时。
  单独启动时，它会自己启动自己的客户端。在另一个容器上完成 setup 的服务器，会在它所挂载到的服务器启动时
  启动自己的客户端。

## <a id="testing"></a>测试

测试在内存客户端启动服务器之前用 `override()` 替换客户端：

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

使用 FastMCP 时测试相同，只是换成 `fastmcp.Client(mcp)`，并且 `result.data == 1`。替换对象永远不会被连接，
所以 `FakeDatabase` 的书来自 `__init__`。

## <a id="errors"></a>错误

- **在 `setup()` 之前添加的工具。** SDK 和 FastMCP 会立即读取工具的签名，而客户端不是它们认识的类型：

  ```
  TypeError: Catalog is a nuke-di client, not a pydantic type. A pydantic model takes it only with arbitrary_types_allowed, and has no JSON schema for it. A framework fills it only as a plain type hint, not as an optional, through its nuke-di integration: e.g. in a FastAPI route declared through nuke_di.fastapi, or in an MCP tool added after setup() of nuke_di.mcp or nuke_di.fastmcp. See https://github.com/troyan-dy/nuke-di#documentation
  ```

- **没有经过 lifespan 就调用的工具**，例如在测试中不通过客户端而直接 `await server.call_tool(...)`：
  SDK 抛出 `UnexpectedToolError: Error executing tool count_books`，FastMCP 抛出 `ToolError: Error calling tool
  'count_books': Failed to resolve dependency 'catalog' for count_books`，二者的原因都是：

  ```
  RuntimeError: Catalog is not connected: run the server with its lifespan, e.g. `async with Client(server)`
  ```

- **在服务器启动之后添加的工具**会抛出 `RuntimeError: Catalog was not started with the server:
  add its tool before the server starts`（FastMCP 中为 "its tool, resource or prompt"）。
- **`connect()` 失败**会让启动失败：`async with Client(server)` 抛出 `RuntimeError: nuke-di
  clients failed to start: Database.connect() raised OSError: db.local:5432 is unreachable`（FastMCP 的客户端
  会在前面加上 `Client failed to connect: `），而 stdio 上的服务器以退出码 1 退出，traceback 中带有该错误，
  宿主会把它报告为启动失败的服务器。
- **对同一个服务器调用两次 `setup()`**：`TypeError: setup() was already called for this app`。
- **两个共享同一函数的 FastMCP 服务器同时运行：** 第二个会启动失败，并抛出 `RuntimeError:
  nuke-di clients failed to start: Catalog is filled for another app that is running; apps that share a
  handler function run one at a time`。
- **在 `setup()` 之前就被读取的 FastMCP 函数。** 把 `setup()` 提前，能在下一个进程中修复上面的 `TypeError`，
  但在已经读取过该函数的进程中不行：FastMCP 会保留它读到的签名。在该进程中稍后把这个函数添加到已完成 setup
  的服务器上，会抛出 `TypeError: FastMCP read count_books before setup() and keeps the signature it read for
  good: call setup() before the function is first added to any server, and start the process again`。
- **在其所挂载到的服务器调用 `setup()` 之前就挂载的服务器**，若使用同一个容器，会让启动失败：
  `RuntimeError: nuke-di clients failed to start: shelf is mounted on a server that runs on the same
  container, but not through it: call setup() of that server before mount()`。
- **不接收客户端的形式**在添加时会抛出 `TypeError`：`Argument "catalog" of a functools.partial of
  count_books is Catalog: nuke-di fills the clients of a function, a bound method or a callable object, so
  declare a function instead`；在 FastMCP 中，对于 `Depends(...)` 中的类和可调用对象，是 `Argument "db" of
  the class Reader is Database: ...`；在 SDK 中，是 `ask takes clients and returns an InputRequiredResult:
  nuke-di fills clients through the SDK's Resolve(...), which the SDK does not combine with an
  InputRequiredResult of the tool itself; such a tool takes no clients`。
- **FastAPI 和 FastMCP 共用一个函数：** `TypeError: count_books takes clients in both FastAPI and FastMCP
  handlers: nuke-di rewrites its signature for one framework, so give each framework its own function`。
