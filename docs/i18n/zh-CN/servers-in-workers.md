# <a id="servers-inside-a-worker"></a>worker 中的服务器

[English](../../guide/servers-in-workers.md) · [Русский](../ru/servers-in-workers.md) · **简体中文** · [Español](../es/servers-in-workers.md) · [Português (Brasil)](../pt-BR/servers-in-workers.md) · [日本語](../ja/servers-in-workers.md) · [Polski](../pl/servers-in-workers.md)

← [文档](../README.zh-CN.md#documentation)

grpc.aio、aiohttp、websockets、APScheduler、Textual 和 Temporal 都没有自己的依赖注入，因此 nuke-di 没有为它们提供模块，
也不需要：服务器在 [`@worker`](workers-and-jobs.md#your-first-worker) 中运行，而以方法充当其处理函数的那个类就是一个 `Client`。

## <a id="the-pattern"></a>模式

- **处理函数是客户端的方法。** gRPC 的 servicer、一组 aiohttp 视图、WebSocket 处理函数、一组 APScheduler 任务、
  Temporal 的 activity：每一个都在 `__init__` 中接收自己的客户端，worker 则通过类型提示接收它。
  它在服务器启动之前被解析，它的客户端也在此之前连接好；处理函数通过 `self` 访问它们。
- **服务器归 worker 所有。** worker 构建并启动服务器，等待
  [`Shutdown`](workers-and-jobs.md#your-first-worker)，停止服务器后返回；随后客户端断开连接。
  退出码、日志和钩子与[任何 worker](workers-and-jobs.md#exit-codes) 相同。
- **服务器在宽限期内从容地停止。** 停止时会让进行中的调用执行完，每种服务器都有自己的时限来控制这一点。
  请让它小于进程运行时的 `SHUTDOWN_GRACE_SECONDS`：后者来自环境变量，而示例把各自的时限写在代码里。
  超过[宽限期](workers-and-jobs.md#grace-period)仍未停止的 worker 会被取消，但处理函数运行在服务器自己的
  任务中，所以取消 worker 并不会让它们停下：它们会继续针对正在断开连接的客户端运行。gRPC 和 APScheduler
  的示例在 `finally` 中中止它们；对于 aiohttp、websockets 和 Temporal，时限是唯一的保障。
- **整个停止过程有一个预算。** 进程最多需要
  `SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × 最长的依赖链`
  才能停止（[宽限期](workers-and-jobs.md#grace-period)）；请给 pod 设置一个大于它的
  `terminationGracePeriodSeconds`（[Kubernetes](workers-and-jobs.md#running-in-kubernetes)）。

| 服务器 | 客户端 | 停止方式 | 默认等待多久 |
|---|---|---|---|
| [grpc.aio](#grpcaio) | servicer | `await server.stop(grace)` | `grace`，必填；`None` 会中止进行中的调用 |
| [aiohttp](#aiohttp) | 一组视图的类 | `await runner.cleanup()` | 最多 2 × `shutdown_timeout`，每次 60 秒 |
| [websockets](#websockets) | 带有连接处理函数的类 | 退出 `async with serve(...)` | `close_timeout`，10 秒，仅限关闭握手；处理函数没有时限 |
| [APScheduler](#apscheduler) | 一组 APScheduler 任务的类 | `scheduler.shutdown()` | 完全不等：正在运行的 APScheduler 任务会被取消 |
| [Textual](#textual) | 无：`App` 从 worker 那里获得客户端 | `app.exit()` | 完全不等 |
| [Temporal](#temporal) | 一组 activity 的类 | 退出 `async with Worker(...)` | `graceful_shutdown_timeout`，0 |

这些示例共用同一个客户端模块：一个查询耗时一秒的数据库，这样在进程被停止时总有一个调用
还在进行中。

```python
# app/clients.py
import asyncio

from nuke_di import Client


class Database(Client):
    def __init__(self) -> None:
        self.genres = {"Dune": "sci-fi", "Emma": "classic", "Neuromancer": "sci-fi"}

    async def connect(self) -> None:
        print("database: connected")

    async def disconnect(self) -> None:
        print("database: disconnected")

    async def count_books(self, genre: str) -> int:
        await asyncio.sleep(1)  # the query
        return sum(1 for book_genre in self.genres.values() if book_genre == genre)
```

每个示例都在一次调用的中途发送 SIGTERM，就像 Kubernetes 停止 pod 那样。调用服务器的程序，例如
`app/grpc_ask.py`，直接在代码中打开连接：它们只是演示用的一次性调用方，不属于应用本身。应用自己持有的连接
是一个客户端，就像[下文](#temporal)中 Temporal 的连接那样。

## <a id="grpcaio"></a>grpc.aio

已在 grpcio 1.84.0 上验证。

```proto
// app/books.proto
syntax = "proto3";

service Books {
  rpc CountBooks (CountRequest) returns (CountReply);
}

message CountRequest {
  string genre = 1;
}

message CountReply {
  int32 count = 1;
}
```

```bash
pip install grpcio grpcio-tools
python -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. app/books.proto
```

servicer 就是客户端：

```python
# app/grpc_server.py
import grpc

from nuke_di import Client, Shutdown, worker

from app import books_pb2, books_pb2_grpc
from app.clients import Database


class BooksService(books_pb2_grpc.BooksServicer, Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def CountBooks(
        self,
        request: books_pb2.CountRequest,
        context: grpc.aio.ServicerContext[books_pb2.CountRequest, books_pb2.CountReply],
    ) -> books_pb2.CountReply:
        print(f"grpc: CountBooks({request.genre!r})")
        count = await self.db.count_books(request.genre)
        print(f"grpc: CountBooks({request.genre!r}) -> {count}")
        return books_pb2.CountReply(count=count)


@worker
async def serve(books: BooksService, shutdown: Shutdown) -> None:
    server = grpc.aio.server()
    books_pb2_grpc.add_BooksServicer_to_server(books, server)
    server.add_insecure_port("localhost:50051")
    await server.start()
    print("grpc: serving on localhost:50051")
    try:
        await shutdown.wait()
        print("grpc: stopping")
        # New calls are refused at once, the calls in flight get 5 seconds: less than SHUTDOWN_GRACE_SECONDS
        await server.stop(grace=5)
    finally:
        # Cancelled past the grace period: abort the calls in flight before the clients disconnect
        await server.stop(None)
    print("grpc: stopped")
```

一个调用它的 job：

```python
# app/grpc_ask.py
import grpc

from nuke_di import job

from app import books_pb2, books_pb2_grpc


@job
async def ask(genre: str = "sci-fi") -> None:
    async with grpc.aio.insecure_channel("localhost:50051") as channel:
        reply = await books_pb2_grpc.BooksStub(channel).CountBooks(books_pb2.CountRequest(genre=genre))
    print(f"ask: {reply.count} {genre} books")
```

```console
$ python -m app.grpc_server &
database: connected
grpc: serving on localhost:50051
$ python -m app.grpc_ask
grpc: CountBooks('sci-fi')
grpc: CountBooks('sci-fi') -> 2
ask: 2 sci-fi books
$ python -m app.grpc_ask --genre classic & sleep 0.8; kill -TERM %1
grpc: CountBooks('classic')
grpc: stopping
grpc: CountBooks('classic') -> 1
grpc: stopped
ask: 1 classic books
database: disconnected
$ wait %1; echo $?
143
```

SIGTERM 在第二次调用的中途到达：这次调用执行完并拿到了响应，然后服务器停止，
最后数据库断开连接。

- `server.stop(grace)` 立即拒绝新的调用，给进行中的调用 `grace` 秒，之后中止它们。
  请让 `grace` 小于 `SHUTDOWN_GRACE_SECONDS`。
- 在项目根目录下用 `-I.` 运行 `protoc`：这样生成的 `books_pb2_grpc.py` 会以
  `from app import books_pb2` 导入。`--pyi_out` 为消息提供类型；mypy 还需要 `pip install types-grpcio`，
  而在 `--strict` 下还需要 mypy-protobuf 为 `books_pb2_grpc.py` 生成的存根：`--mypy_grpc_out=.`。

`finally` 覆盖的是宽限期结束时 worker 仍在停止的情况：nuke-di 取消它，`server.stop(None)` 在客户端断开连接
之前中止进行中的调用。当宽限期比调用更短时：

```console
$ SHUTDOWN_GRACE_SECONDS=0.2 python -m app.grpc_server &
database: connected
grpc: serving on localhost:50051
$ python -m app.grpc_ask --genre classic & sleep 0.6; kill -TERM %1
grpc: CountBooks('classic')
grpc: stopping
Run app.grpc_server.serve did not stop within 0.2s after Shutdown, cancelling it
database: disconnected
Run app.grpc_ask.ask failed
Traceback (most recent call last):
  ...
grpc.aio._call.AioRpcError: <AioRpcError of RPC that terminated with:
	status = StatusCode.UNAVAILABLE
	details = "Cancelling all calls"
	debug_error_string = "UNAVAILABLE:Cancelling all calls"
>
$ wait %1; echo $?
143
```

调用被中止，调用方收到了 `UNAVAILABLE`，而不是继续针对已断开连接的数据库运行。

## <a id="aiohttp"></a>aiohttp

已在 aiohttp 3.14.4 上验证。处理函数是客户端的方法，以绑定方法的形式添加到路由中：

```python
# app/aiohttp_server.py
from aiohttp import web

from nuke_di import Client, Shutdown, worker

from app.clients import Database


class BookViews(Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def count(self, request: web.Request) -> web.Response:
        genre = request.match_info["genre"]
        count = await self.db.count_books(genre)
        print(f"aiohttp: {request.method} {request.path} -> {count}")
        return web.json_response({"genre": genre, "count": count})


@worker
async def serve(views: BookViews, shutdown: Shutdown) -> None:
    app = web.Application()
    app.router.add_get("/books/{genre}/count", views.count)
    # cleanup() waits up to 2 × shutdown_timeout for the requests in flight: keep it below SHUTDOWN_GRACE_SECONDS
    runner = web.AppRunner(app, shutdown_timeout=4)
    await runner.setup()
    await web.TCPSite(runner, "localhost", 8080).start()
    print("aiohttp: serving on http://localhost:8080")
    await shutdown.wait()
    print("aiohttp: stopping")
    await runner.cleanup()
    print("aiohttp: stopped")
```

在一个终端中：

```console
$ python -m app.aiohttp_server
database: connected
aiohttp: serving on http://localhost:8080
aiohttp: GET /books/sci-fi/count -> 2
aiohttp: stopping
aiohttp: GET /books/classic/count -> 1
aiohttp: stopped
database: disconnected
$ echo $?
143
```

在另一个终端中：

```console
$ curl -s -w '\n' localhost:8080/books/sci-fi/count
{"genre": "sci-fi", "count": 2}
$ curl -s -w '\n' localhost:8080/books/classic/count & sleep 0.5; pkill -TERM -f app.aiohttp_server; wait
{"genre": "classic", "count": 1}
```

- `web.run_app()` 无法在 worker 中运行：它会启动自己的事件循环，并自行处理 SIGINT 和 SIGTERM。
  `AppRunner` 和 `TCPSite` 在 worker 的事件循环上运行同一个应用，并把信号留给 nuke-di 处理。
- `cleanup()` 最多等待 `shutdown_timeout` 让进行中的请求完成，然后取消它，并再等待最多 `shutdown_timeout`：
  总共最多是超时的两倍，而超时默认是 60 秒。请让两倍的超时小于 `SHUTDOWN_GRACE_SECONDS`，就像 4 秒的两倍
  小于默认的 10 秒那样。`finally` 在这里帮不上忙：nuke-di 的取消落在 `cleanup()` 内部，进行中的处理函数
  会继续针对正在断开连接的客户端运行。
- `on_shutdown` 回调在 `cleanup()` 开始时、等待进行中的请求之前运行：长连接的 WebSocket 或
  server-sent events 连接就在这里关闭。`on_startup`、`on_cleanup` 和 `cleanup_ctx` 分别在 `setup()` 和
  `cleanup()` 中运行，此时客户端处于连接状态。

## <a id="websockets"></a>websockets

已在 websockets 17.2 上验证，使用其 asyncio 实现 `websockets.asyncio.server`；`websockets.legacy`
自 14.0 起已弃用。

```python
# app/websocket_server.py
from websockets.asyncio.server import ServerConnection, serve as serve_websockets
from websockets.exceptions import ConnectionClosed

from nuke_di import Client, Shutdown, worker

from app.clients import Database


class BookSocket(Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def handle(self, websocket: ServerConnection) -> None:
        # One call per connection; it returns when the connection closes
        try:
            while True:
                genre = await websocket.recv(decode=True)
                count = await self.db.count_books(genre)
                print(f"websockets: {genre} -> {count}")
                await websocket.send(str(count))
        except ConnectionClosed as closed:
            print(f"websockets: {closed}")


@worker
async def serve(socket: BookSocket, shutdown: Shutdown) -> None:
    # close_timeout: how long a client may take to answer the close, 10 seconds by default
    async with serve_websockets(socket.handle, "localhost", 8765, close_timeout=2):
        print("websockets: serving on ws://localhost:8765")
        await shutdown.wait()
        print("websockets: stopping")
    # Leaving the block refuses new connections, closes the open ones with 1001 (going away)
    # and waits for their handlers to return
    print("websockets: stopped")
```

```python
# app/websocket_ask.py
from websockets.asyncio.client import connect

from nuke_di import job


@job
async def ask(genre: list[str]) -> None:
    async with connect("ws://localhost:8765") as websocket:
        for name in genre:
            await websocket.send(name)
            print(f"ask: {await websocket.recv(decode=True)} {name} books")
```

在一个终端中：

```console
$ python -m app.websocket_server
database: connected
websockets: serving on ws://localhost:8765
websockets: sci-fi -> 2
websockets: classic -> 1
websockets: received 1000 (OK); then sent 1000 (OK)
websockets: sci-fi -> 2
websockets: stopping
websockets: classic -> 1
websockets: sent 1001 (going away); then received 1001 (going away)
websockets: stopped
database: disconnected
$ echo $?
143
```

在另一个终端中：

```console
$ python -m app.websocket_ask --genre sci-fi --genre classic
ask: 2 sci-fi books
ask: 1 classic books
$ python -m app.websocket_ask --genre sci-fi --genre classic & sleep 1.8; pkill -TERM -f app.websocket_server; wait
ask: 2 sci-fi books
Run app.websocket_ask.ask failed
Traceback (most recent call last):
  ...
websockets.exceptions.ConnectionClosedOK: received 1001 (going away); then sent 1001 (going away)
```

- 退出 `serve()` 会立即以 1001（going away）关闭所有打开的连接。与 gRPC 和 aiohttp 不同，
  进行中的消息会丢失它的回复：`classic -> 1` 已经算出，却从未发送。上面的调用方在收到 1001 时失败；
  真正的客户端应当在收到它后重连，例如用 `async for websocket in connect(...)`，并发送可以安全重发的消息。
- `close_timeout` 默认是 10 秒，只限制与每个客户端的关闭握手，但也请调低它：默认值等于整个默认宽限期。
  处理函数没有任何时限：退出 `serve()` 会等待每一个处理函数返回，因此一个卡住的处理函数会让 worker
  超出宽限期，并在 worker 被取消后继续针对正在断开连接的客户端运行。

## <a id="apscheduler"></a>APScheduler

已在当前版本 APScheduler 3.11.3 上验证。APScheduler 任务是客户端的方法：

```python
# app/scheduler.py
import asyncio
import contextlib
from collections.abc import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from nuke_di import Client, Shutdown, worker

from app.clients import Database


class Reports(Client):
    def __init__(self, db: Database) -> None:
        self.db = db
        self._run = asyncio.Lock()

    async def count_sci_fi(self) -> None:
        async with self._run:
            print("reports: counting")
            print(f"reports: {await self.db.count_books('sci-fi')} sci-fi books")

    @contextlib.asynccontextmanager
    async def between_runs(self) -> AsyncIterator[None]:
        # Wait for the run in flight, if any, and keep the next one from starting
        async with self._run:
            yield


@worker
async def schedule(reports: Reports, shutdown: Shutdown) -> None:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(reports.count_sci_fi, "interval", seconds=2)
    scheduler.start()
    print("scheduler: started")
    try:
        await shutdown.wait()
        scheduler.pause()  # no new runs
        async with reports.between_runs():  # the run in flight finishes
            pass
    finally:
        # Also when the worker is cancelled past the grace period. shutdown() only schedules the stop,
        # and the stop cancels the coroutine jobs still running, whatever `wait` says
        scheduler.shutdown()
        await asyncio.sleep(0)  # the stop runs here, before the clients disconnect
    print("scheduler: stopped")
```

在第二次运行期间按下 Ctrl+C：

```console
$ python -m app.scheduler
database: connected
scheduler: started
reports: counting
reports: 2 sci-fi books
reports: counting
^C
reports: 2 sci-fi books
scheduler: stopped
database: disconnected
$ echo $?
130
```

- `AsyncIOScheduler.shutdown()` 无法等待协程任务：它只是在事件循环上安排停止，而无论 `wait=` 怎么设置，
  停止都会取消仍在运行的 APScheduler 任务。worker 先暂停调度器，使新的运行不再开始，并在
  `reports.between_runs()` 中等待进行中的那次运行；之后才关闭调度器。没有这次等待，同样的 Ctrl+C 会让这次
  运行在 `count_books` 中以 `asyncio.exceptions.CancelledError` 结束。
- worker 被取消时，`finally` 同样会关闭调度器，例如某次运行比宽限期更长：这次运行在数据库断开连接之前
  被取消，而不是继续针对它运行。`await asyncio.sleep(0)` 让已安排的停止在 worker 返回之前发生。
- 调度器放在 worker 中，而不是放在一个独立的客户端里：调度器客户端会在每个解析它的容器中启动，包括 Web
  应用的容器；而 worker 掌控停止的顺序，先是进行中的运行，然后才是调度器。APScheduler 任务是客户端的方法，
  因此它们通过 `self` 访问数据库，并共享 `between_runs()` 的锁。用 `DI.inject()` 绑定、传给 `add_job()` 的
  模块级函数同样可行，只要在 worker 启动之前调用 `inject()`：在 worker 内部容器已经连接好了，
  `inject()` 会失败。

### <a id="apscheduler-4"></a>APScheduler 4

APScheduler 4 是 alpha 版本，已在 4.0.0a6 上验证，它的 API 在 4.0 发布之前仍可能变化。`AsyncScheduler`
是一个异步上下文管理器，调度通过 `await add_schedule()` 添加。`Reports` 保持不变；导入和 worker 变为：

```python
# app/scheduler.py, on APScheduler 4
from apscheduler import AsyncScheduler
from apscheduler.triggers.interval import IntervalTrigger


@worker
async def schedule(reports: Reports, shutdown: Shutdown) -> None:
    # Leaving the block stops the scheduler, also when the worker is cancelled past the grace period
    async with AsyncScheduler() as scheduler:
        await scheduler.add_schedule(reports.count_sci_fi, IntervalTrigger(seconds=2), id="count-sci-fi")
        await scheduler.start_in_background()
        print("scheduler: started")
        await shutdown.wait()
        async with reports.between_runs():  # the run in flight finishes
            await scheduler.stop()
    print("scheduler: stopped")
```

```console
$ python -m app.scheduler
database: connected
scheduler: started
reports: counting
reports: 2 sci-fi books
reports: counting
reports: 2 sci-fi books
reports: counting
^C
reports: 2 sci-fi books
scheduler: stopped
database: disconnected
$ echo $?
130
```

APScheduler 4 会立即运行一次间隔调度，之后每 2 秒运行一次。它的 `stop()` 同样会取消正在运行的
APScheduler 任务，而且输出中不留任何痕迹，因此等待进行中的运行这一步仍然需要。

## <a id="textual"></a>Textual

已在 Textual 8.2.8 上验证。Textual 应用不是客户端：worker 构建它，并把自己接收到的客户端传给它，
就像测试传入 mock 那样。

```python
# app/tui.py
from textual.app import App, ComposeResult
from textual.widgets import Footer, Static

from nuke_di import BackgroundTasks, Shutdown, worker

from app.clients import Database


class BooksApp(App[None]):
    BINDINGS = [("r", "refresh", "Refresh"), ("q", "quit", "Quit")]

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db

    def compose(self) -> ComposeResult:
        yield Static("counting...", id="count")
        yield Footer()

    async def on_mount(self) -> None:
        await self.action_refresh()

    async def action_refresh(self) -> None:
        count = await self.db.count_books("sci-fi")
        self.query_one("#count", Static).update(f"{count} sci-fi books")


async def exit_on_shutdown(app: BooksApp, shutdown: Shutdown) -> None:
    await shutdown.wait()
    app.exit()


@worker
async def tui(db: Database, tasks: BackgroundTasks, shutdown: Shutdown) -> None:
    app = BooksApp(db)
    tasks.spawn(exit_on_shutdown(app, shutdown))
    await app.run_async()
    print("tui: closed")
```

应用会占据终端，这里是 60 列 × 8 行：

```text
2 sci-fi books






 r Refresh  q Quit                              ▏^p palette
```

按 `q` 关闭它，进程以 `0` 退出：

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
0
```

SIGTERM，例如在另一个终端中执行 `pkill -TERM -f app.tui`，会通过 `exit_on_shutdown` 关闭它：

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
143
```

- `App.run()` 会启动自己的事件循环；在 worker 中，`await app.run_async()` 在 worker 的事件循环上运行应用。
  当应用自行关闭时，`exit_on_shutdown` 仍在等待：`BackgroundTasks` 在断开连接时取消它，这发生在 worker
  返回之后。
- Textual 会关闭终端的信号键：Ctrl+C 是一个按键，它会询问 "Do you want to quit? Press ctrl+q
  to quit the app"，而不是 SIGINT。设置 `TEXTUAL_ALLOW_SIGNALS=1` 后，Ctrl+C 会发送 SIGINT：应用关闭，
  进程以 `130` 退出。无论哪种情况，SIGTERM 都会到达 nuke-di。
- 应用运行期间，Textual 会捕获 `print()`；worker 在 `run_async()` 返回后才打印。Textual 自己的
  worker，即 `@work` 和 `run_worker()`，运行在应用内部，与 `@worker` 毫无关系。

应用在 `__init__` 中接收客户端，因此测试可以借助 Textual 的 `run_test()` 传入一个 mock，
与[测试](testing.md)中一样在 pytest-asyncio 下使用 `asyncio_mode = auto`：

```python
# tests/test_tui.py
from unittest.mock import AsyncMock

from textual.widgets import Static

from app.clients import Database
from app.tui import BooksApp


async def test_refresh_shows_the_count() -> None:
    db = AsyncMock(spec=Database)
    db.count_books.side_effect = [2, 3]
    app = BooksApp(db)
    async with app.run_test() as pilot:
        assert app.query_one("#count", Static).content == "2 sci-fi books"
        await pilot.press("r")
        assert app.query_one("#count", Static).content == "3 sci-fi books"
```

```console
$ pytest -q tests/test_tui.py
.                                                                        [100%]
1 passed in 0.20s
```

## <a id="temporal"></a>Temporal

已在 temporalio 1.34.0 和 Temporal CLI 1.9.1 的开发服务器上验证。activity 负责 I/O，而 Temporal
为它们提供数据库的方式，是一个以方法作为 activity 的类，每个 Temporal worker 只构建一次。这个类就是客户端：

```python
# app/books/activities.py
from temporalio import activity

from nuke_di import Client

from app.clients import Database


class BookActivities(Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    @activity.defn
    async def count_books(self, genre: str) -> int:
        print(f"activity: count_books({genre!r})")
        count = await self.db.count_books(genre)
        print(f"activity: count_books({genre!r}) -> {count}")
        return count
```

到 Temporal 的连接也是一个客户端，它在 `connect()` 中创建 temporalio 的 `Client`，与任何
[第三方对象](../../adr/0005-third-party-objects-as-client-classes.md)一样：

```python
# app/books/temporal.py
import os

from temporalio.client import Client as TemporalClient

from nuke_di import Client


class Temporal(Client):
    def __init__(self) -> None:
        self.address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
        self._client: TemporalClient | None = None

    async def connect(self) -> None:
        self._client = await TemporalClient.connect(self.address)
        print(f"temporal: connected to {self.address}")

    async def disconnect(self) -> None:
        # temporalio has no close(): the connection goes with the object
        self._client = None
        print("temporal: disconnected")

    @property
    def client(self) -> TemporalClient:
        if self._client is None:
            raise RuntimeError("Temporal is not connected")
        return self._client
```

workflow 放在单独的模块中，worker 和启动它的 job 都会导入这个模块：

```python
# app/books/workflows.py
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.books.activities import BookActivities


@workflow.defn
class CountBooks:
    @workflow.run
    async def run(self, genre: str) -> int:
        return await workflow.execute_activity_method(
            BookActivities.count_books, genre, start_to_close_timeout=timedelta(seconds=10)
        )
```

```python
# app/books/worker.py
from datetime import timedelta

from temporalio.worker import Worker

from nuke_di import Shutdown, worker

from app.books.activities import BookActivities
from app.books.temporal import Temporal
from app.books.workflows import CountBooks


@worker
async def books(temporal: Temporal, activities: BookActivities, shutdown: Shutdown) -> None:
    async with Worker(
        temporal.client,
        task_queue="books",
        workflows=[CountBooks],
        activities=[activities.count_books],
        # The activities in flight get 5 seconds: Temporal's default of 0 cancels them at once
        graceful_shutdown_timeout=timedelta(seconds=5),
    ):
        print("temporal: polling the books task queue")
        await shutdown.wait()
        print("temporal: stopping")
    print("temporal: stopped")
```

```python
# app/books/start.py
from nuke_di import job

from app.books.temporal import Temporal
from app.books.workflows import CountBooks


@job
async def start(temporal: Temporal, genre: str = "sci-fi") -> None:
    count = await temporal.client.execute_workflow(CountBooks.run, genre, id=f"count-books-{genre}", task_queue="books")
    print(f"start: {count} {genre} books")
```

```bash
pip install temporalio
temporal server start-dev  # in a terminal of its own
```

worker 在第二个 activity 执行到一半时被停止，然后再次启动：

```console
$ python -m app.books.worker
database: connected
temporal: connected to localhost:7233
temporal: polling the books task queue
activity: count_books('sci-fi')
activity: count_books('sci-fi') -> 2
activity: count_books('classic')
temporal: stopping
activity: count_books('classic') -> 1
temporal: stopped
temporal: disconnected
database: disconnected
$ echo $?
143
$ python -m app.books.worker
database: connected
temporal: connected to localhost:7233
temporal: polling the books task queue
^C
temporal: stopping
temporal: stopped
temporal: disconnected
database: disconnected
```

在另一个终端中运行 workflow：

```console
$ python -m app.books.start
temporal: connected to localhost:7233
start: 2 sci-fi books
temporal: disconnected
$ python -m app.books.start --genre classic & sleep 1; pkill -TERM -f app.books.worker; wait
temporal: connected to localhost:7233
start: 1 classic books
temporal: disconnected
```

进行中的 activity 在 `graceful_shutdown_timeout` 内执行完毕，Temporal 保存了它的结果。worker
在 workflow 取走这个结果之前就已停止，所以 `start` 一直在等待；下一个 worker 进程完成了这个 workflow，
而没有再次运行该 activity。

- `graceful_shutdown_timeout` 默认是 0：worker 一关闭，进行中的 activity 就会被取消。
  请把它设置得远小于 `SHUTDOWN_GRACE_SECONDS`：如果 nuke-di 在退出 `async with Worker(...)` 仍在关闭时
  取消 worker，activity 就会继续针对正在断开连接的客户端运行。
- temporalio 的 `Client` 与 nuke-di 的同名：请以 `TemporalClient` 的名字导入它。
- Temporal 的 `Worker` 以已解析实例的绑定方法接收 activity，即 `activities=[activities.count_books]`；
  workflow 则通过类的方法 `BookActivities.count_books` 引用它们。

### <a id="workflows-never-take-clients"></a>workflow 从不接收客户端

workflow 是 Temporal 会根据历史记录重放的代码，可能在任何 worker 上、任何时刻、在沙箱中重放：它必须是
确定性的，并且不做任何 I/O。因此 workflow 从不接收客户端。`CountBooks` 的 `__init__` 没有参数，
由 Temporal 构建，nuke-di 从不接触它；凡是要访问数据库、HTTP API 或队列的，都是 activity。
workflow 模块导入 `BookActivities` 只是为了引用它的方法，并放在
`workflow.unsafe.imports_passed_through()` 之下，这样沙箱会使用已经导入的模块，而不是再导入一次。

测试在 Temporal 的时间跳跃（time-skipping）测试服务器上运行 workflow，activity 则围绕一个 mock
手动构建，与[测试](testing.md)中一样在 pytest-asyncio 下使用 `asyncio_mode = auto`：

```python
# tests/test_books.py
from unittest.mock import AsyncMock

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.books.activities import BookActivities
from app.books.workflows import CountBooks
from app.clients import Database


async def test_count_books() -> None:
    db = AsyncMock(spec=Database)
    db.count_books.return_value = 7
    activities = BookActivities(db)
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(env.client, task_queue="test", workflows=[CountBooks], activities=[activities.count_books]),
    ):
        count = await env.client.execute_workflow(CountBooks.run, "sci-fi", id="test", task_queue="test")

    assert count == 7
    db.count_books.assert_awaited_once_with("sci-fi")
```

```console
$ pytest -q tests/test_books.py
.                                                                        [100%]
1 passed in 0.33s
```

第一次运行时会下载测试服务器。
