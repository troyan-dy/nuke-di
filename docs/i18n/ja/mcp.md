# <a id="mcp-servers"></a>MCP サーバー

[English](../../guide/mcp.md) · [Русский](../ru/mcp.md) · [简体中文](../zh-CN/mcp.md) · [Español](../es/mcp.md) · [Português (Brasil)](../pt-BR/mcp.md) · **日本語** · [Polski](../pl/mcp.md)

← [ドキュメント](../README.ja.md#documentation)

MCP ツールは、リクエストの `Context` を受け取るのと同じように、型ヒントでクライアントを受け取ります。クライアントは LLM が見る入力スキーマには含まれません。クライアントはサーバーの起動時に接続し、停止時に切断します。モジュールはライブラリごとに 1 つずつ、計 2 つです。公式 SDK の `MCPServer` には `nuke_di.mcp`、FastMCP には `nuke_di.fastmcp` を使います。

## <a id="the-mcp-sdk"></a>MCP SDK

```bash
pip install "nuke-di[mcp]"
```

`mcp` 2.0 以降が必要です。その `MCPServer` は、`Annotated[T, Resolve(fn)]` と注釈されたツール引数を、`fn` を呼び出して埋めます。`nuke_di.mcp` はこの仕組みでクライアントを渡します。`mcp` 1.x の `FastMCP` クラスにはこうした仕組みがないため、サポートしていません。ツールが 1 つだけの書店の例です。

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

Claude Desktop や IDE などの MCP ホストは、`python -m bookshop.server` でサーバーを起動し、stdio 経由で通信します。次のスクリプトも、SDK のクライアントを使って同じことをします。

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

入力スキーマにあるのは `genre` だけです。`INFO` の行は、サーバーの stderr に出力された `nuke-di` の起動の要約です。ロギングは SDK が設定しています。

ルール：

- **クライアントが埋められる場所。** `setup(server)` の後に `@server.tool()` または `server.add_tool()` で追加したツールの引数と、それらが使うすべてのリゾルバー `Annotated[T, Resolve(fn)]` の引数です。深さは問わず、関数、バウンドメソッド、呼び出し可能オブジェクトのいずれでも構いません。`catalog: Catalog = None` のような素のデフォルト値を持つ引数も埋められます。それ以外の引数（ツールの入力、`Context`）はすべて SDK が扱います。自前の `Resolve(...)` を付けた引数には、型がクライアントであっても手を付けません。
- **ツールのみ。** SDK にはリソースとプロンプト用のリゾルバーがないため、これらはクライアントを受け取りません。データはツールの中で読むか、[FastMCP](#fastmcp) を使ってください。`functools.partial` と、`InputRequiredResult` を返すツール（SDK はこれをリゾルバーと組み合わせません）もクライアントを受け取りません。どちらも追加時に `TypeError` を送出します。
- **起動するクライアント。** 起動時に、`setup()` を通じて追加されたすべてのツールのクライアントが起動します。事前に作成して `MCPServer(tools=[...])` として渡した `Tool` オブジェクトは認識されません。
- **lifespan。** クライアントはサーバー自身の `lifespan=` より前に接続し、その後に切断するので、lifespan からクライアントを使えます。SDK は lifespan を、stdio ではプロセスごとに 1 回、streamable HTTP ではアプリごとに 1 回、インメモリの `Client` ではその `Client` ごとに 1 回実行します。SSE トランスポートでは接続ごとに実行するため、同時に 2 つ目の接続があると起動に失敗します。streamable HTTP で提供してください。インメモリの `Client(server)` を同時に 2 つ開いた場合も同じように失敗します。
- **関数は関数のまま。** `Resolve` マーカーがアノテーションに入っているのは、ツールの追加時に SDK がそれを読む間だけです。テストでは、自分で用意したクライアントを渡して呼び出せます。

## <a id="fastmcp"></a>FastMCP

```bash
pip install "nuke-di[fastmcp]"
```

FastMCP 4.0 以降が必要です。FastMCP は、デフォルト値が `Depends(fn)` である引数を埋め、スキーマから除外します。ツール、リソース、プロンプトのいずれでも同じです。`nuke_di.fastmcp` は、すべてのクライアント引数にこうしたデフォルト値を与えます。依存関係、リソース、プロンプトを加えた同じ書店の例です。

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

残りには、FastMCP 自身のインメモリクライアントからアクセスできます。

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

ルール：

- **クライアントが埋められる場所。** `setup(mcp)` の後に、サーバーのデコレーター、またはその `add_tool()`、`add_resource()`、`add_prompt()` で関数またはバウンドメソッドとして追加したツール、リソース、プロンプトの引数と、それらが使う `Depends(...)` 内のすべての関数の引数です。深さは問いません。`catalog: Catalog = None` のような素のデフォルト値を持つ引数も埋められます。`Depends(...)` や FastMCP の他のマーカーを持つ引数は、型がクライアントであってもそのままにします。`mcp.add_tool(Tool.from_function(fn))` のように事前に作成した `Tool` オブジェクトは認識されません。クライアントを受け取る `functools.partial`、呼び出し可能オブジェクト、`Depends(...)` 内のクラスは `TypeError` を送出します。関数として宣言してください。
- **シグネチャが変わる。** FastMCP は `Depends` をデフォルト値からしか読まないため、クライアント引数はキーワード専用になって、呼び出し側が渡す引数の後ろに置かれ、デフォルト値として `Depends(...)` を持ちます（`count_books(genre, *, catalog=Depends(...))`）。これが見えるのはイントロスペクションだけで、テストでは書いたとおりの関数を呼び出します。
- **lifespan。** クライアントはサーバー自身の `lifespan=` より前に接続し、その後に切断します。FastMCP は、サーバーを共有するセッションやトランスポートがいくつあっても、lifespan を 1 回だけ実行します。
- **一度に 1 つのサーバー。** FastMCP は関数のシグネチャを保持し続けるため、関数はコンテナに関係なく一度だけ書き換えられます。そのため、ツール関数を共有する 2 つのサーバーは 1 つずつ順番に実行します。1 つ目の実行中に起動した 2 つ目のサーバーは、起動に失敗します。同じ理由で、1 つの関数は FastMCP か FastAPI のどちらか一方にしか使えません。
- **マウントされたサーバー。** 関数がクライアントを受け取るすべてのサーバーで `setup()` を呼び、その後に `mount()` を呼びます。同じコンテナでマウント先のサーバーにマウントされたサーバーは、マウント先によって起動されます。両方のクライアントは、実行するサーバーの起動時に 1 回だけ接続します。単独で起動した場合は、自分のクライアントを自分で起動します。別のコンテナで設定されたサーバーは、マウント先のサーバーの起動時に自分のクライアントを起動します。

## <a id="testing"></a>テスト

テストでは、インメモリクライアントがサーバーを起動する前に、`override()` でクライアントを差し替えます。

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

FastMCP でも、`fastmcp.Client(mcp)` を使い `result.data == 1` を確かめる点を除けば、テストは同じです。Replacement は接続されないので、`FakeDatabase` は `__init__` で本を持たせています。

## <a id="errors"></a>エラー

- **`setup()` より前に追加されたツール。** SDK も FastMCP もツールのシグネチャをその場で読みますが、クライアントはそれらが知っている型ではありません。

  ```
  TypeError: Catalog is a nuke-di client, not a pydantic type. A pydantic model takes it only with arbitrary_types_allowed, and has no JSON schema for it. A framework fills it only as a plain type hint, not as an optional, through its nuke-di integration: e.g. in a FastAPI route declared through nuke_di.fastapi, or in an MCP tool added after setup() of nuke_di.mcp or nuke_di.fastmcp. See https://github.com/troyan-dy/nuke-di#documentation
  ```

- **lifespan なしで呼び出されたツール**（テストでクライアントを使わずに `await server.call_tool(...)` を呼ぶ場合など）。SDK は `UnexpectedToolError: Error executing tool count_books` を、FastMCP は `ToolError: Error calling tool 'count_books': Failed to resolve dependency 'catalog' for count_books` を送出します。どちらも原因は次のエラーです。

  ```
  RuntimeError: Catalog is not connected: run the server with its lifespan, e.g. `async with Client(server)`
  ```

- **サーバーの起動後に追加されたツール**は、`RuntimeError: Catalog was not started with the server: add its tool before the server starts` を送出します（FastMCP では "its tool, resource or prompt"）。
- **`connect()` の失敗**は起動を失敗させます。`async with Client(server)` は `RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: db.local:5432 is unreachable` を送出し（FastMCP のクライアントは先頭に `Client failed to connect: ` を付けます）、stdio のサーバーはそのエラーをトレースバックに含めて終了コード 1 で終了します。ホストはこれを、起動に失敗したサーバーとして報告します。
- 1 つのサーバーに対する **2 回目の `setup()`**：`TypeError: setup() was already called for this app`。
- **関数を共有する 2 つの FastMCP サーバーの同時実行：** 2 つ目は `RuntimeError: nuke-di clients failed to start: Catalog is filled for another app that is running; apps that share a handler function run one at a time` で起動に失敗します。
- **`setup()` より前に FastMCP が読んだ関数。** `setup()` を上に移すと、上記の `TypeError` は次のプロセスでは直りますが、すでに関数を読んだプロセスでは直りません。FastMCP は読んだシグネチャを保持するためです。そのプロセスで後からその関数を設定済みのサーバーに追加すると、`TypeError: FastMCP read count_books before setup() and keeps the signature it read for good: call setup() before the function is first added to any server, and start the process again` を送出します。
- **マウント先のサーバーの `setup()` より前にマウントされたサーバー**は、同じコンテナの場合、起動を失敗させます：`RuntimeError: nuke-di clients failed to start: shelf is mounted on a server that runs on the same container, but not through it: call setup() of that server before mount()`。
- **クライアントを受け取れない形**は、追加時に `TypeError` を送出します：`Argument "catalog" of a functools.partial of count_books is Catalog: nuke-di fills the clients of a function, a bound method or a callable object, so declare a function instead`。FastMCP では、`Depends(...)` 内のクラスと呼び出し可能オブジェクトに対して `Argument "db" of the class Reader is Database: ...`。SDK では `ask takes clients and returns an InputRequiredResult: nuke-di fills clients through the SDK's Resolve(...), which the SDK does not combine with an InputRequiredResult of the tool itself; such a tool takes no clients`。
- **FastAPI と FastMCP で 1 つの関数を使う場合：** `TypeError: count_books takes clients in both FastAPI and FastMCP handlers: nuke-di rewrites its signature for one framework, so give each framework its own function`。
