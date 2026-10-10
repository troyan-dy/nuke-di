# <a id="servers-inside-a-worker"></a>ワーカーの中で動くサーバー

[English](../../guide/servers-in-workers.md) · [Русский](../ru/servers-in-workers.md) · [简体中文](../zh-CN/servers-in-workers.md) · [Español](../es/servers-in-workers.md) · [Português (Brasil)](../pt-BR/servers-in-workers.md) · **日本語** · [Polski](../pl/servers-in-workers.md)

← [ドキュメント](../README.ja.md#documentation)

grpc.aio、aiohttp、websockets、APScheduler、Textual、Temporal には独自の依存性注入がありません。そのため nuke-di にはこれらのためのモジュールがなく、必要もありません。サーバーは [`@worker`](workers-and-jobs.md#your-first-worker) の中で動き、そのハンドラーをメソッドとして持つクラスが `Client` になります。

## <a id="the-pattern"></a>パターン

- **ハンドラーはクライアントのメソッドです。** gRPC のサービサー、aiohttp のビューをまとめたクラス、WebSocket のハンドラー、定期実行するジョブのクラス、Temporal のアクティビティ。どれも `__init__` で自分のクライアントを受け取り、ワーカーはそれを型ヒントで受け取ります。サーバーが起動する前にそれが解決され、そのクライアントが接続されるので、ハンドラーは `self` を通じてクライアントにアクセスできます。
- **サーバーはワーカーのものです。** ワーカーがサーバーを組み立てて起動し、[`Shutdown`](workers-and-jobs.md#your-first-worker) を待ち、サーバーを止めてから戻ります。そのあとクライアントが切断されます。終了コード、ログ、フックは[ほかのワーカー](workers-and-jobs.md#exit-codes)と同じです。
- **サーバーは猶予期間内に停止します。** 停止の際には処理中の呼び出しが終わるのを待ちますが、その待ち時間のタイムアウトはサーバーごとに異なります。これは `SHUTDOWN_GRACE_SECONDS` より短くしてください。[猶予期間](workers-and-jobs.md#grace-period)を過ぎてもまだ停止中のワーカーはキャンセルされ、処理中の呼び出しも一緒にキャンセルされます。

| サーバー | クライアント | 停止の方法 | タイムアウト（デフォルト） |
|---|---|---|---|
| [grpc.aio](#grpcaio) | サービサー | `await server.stop(grace)` | `grace`、必須。`None` だと処理中の呼び出しを打ち切る |
| [aiohttp](#aiohttp) | ビューのクラス | `await runner.cleanup()` | `shutdown_timeout`、60 秒 |
| [websockets](#websockets) | 接続ハンドラーを持つクラス | `async with serve(...)` を抜ける | `close_timeout`、10 秒 |
| [APScheduler](#apscheduler) | ジョブのクラス | `scheduler.shutdown()` | タイムアウトなし：実行中のジョブはキャンセルされる |
| [Textual](#textual) | なし：`App` はワーカーからクライアントを受け取る | `app.exit()` | タイムアウトなし |
| [Temporal](#temporal) | アクティビティのクラス | `async with Worker(...)` を抜ける | `graceful_shutdown_timeout`、0 |

どのレシピも、同じクライアントモジュールを使います。クエリに 1 秒かかるデータベースで、プロセスを止めた時点でまだ呼び出しが処理中になるようにしています。

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

どのレシピでも、Kubernetes が Pod を止めるときと同じように、呼び出しの最中に SIGTERM を送ります。

## <a id="grpcaio"></a>grpc.aio

grpcio 1.84.0 で確認しています。

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

サービサーがクライアントです。

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
        self, request: books_pb2.CountRequest, context: grpc.aio.ServicerContext
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
    await shutdown.wait()
    print("grpc: stopping")
    # New calls are refused at once, the calls in flight get 5 seconds: less than SHUTDOWN_GRACE_SECONDS
    await server.stop(grace=5)
    print("grpc: stopped")
```

これを呼び出すジョブです。

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

SIGTERM は 2 回目の呼び出しの最中に届きました。呼び出しは最後まで処理されて応答を受け取り、そのあとサーバーが停止し、最後にデータベースが切断されました。

- `server.stop(grace)` は新しい呼び出しをただちに拒否し、処理中の呼び出しに `grace` 秒の猶予を与えてから打ち切ります。`grace` は `SHUTDOWN_GRACE_SECONDS` より短くしてください。そうしないと、先に nuke-di がワーカーをキャンセルします。
- `protoc` はプロジェクトのルートから `-I.` を付けて実行します。こうすると生成される `books_pb2_grpc.py` は `from app import books_pb2` でインポートするようになり、`--pyi_out` によって型チェッカーがメッセージのクラスを認識できます。

## <a id="aiohttp"></a>aiohttp

aiohttp 3.14.4 で確認しています。ハンドラーはクライアントのメソッドで、バウンドメソッドとしてルートに追加します。

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
    # The requests in flight get 5 seconds: aiohttp's default of 60 is longer than SHUTDOWN_GRACE_SECONDS
    runner = web.AppRunner(app, shutdown_timeout=5)
    await runner.setup()
    await web.TCPSite(runner, "localhost", 8080).start()
    print("aiohttp: serving on http://localhost:8080")
    await shutdown.wait()
    print("aiohttp: stopping")
    await runner.cleanup()
    print("aiohttp: stopped")
```

1 つ目のターミナル：

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

もう 1 つのターミナル：

```console
$ curl -s -w '\n' localhost:8080/books/sci-fi/count
{"genre": "sci-fi", "count": 2}
$ curl -s -w '\n' localhost:8080/books/classic/count & sleep 0.5; pkill -TERM -f app.aiohttp_server; wait
{"genre": "classic", "count": 1}
```

- `web.run_app()` はワーカーの中では使えません。独自のイベントループを起動し、SIGINT と SIGTERM を自分で処理するからです。`AppRunner` と `TCPSite` を使えば、同じアプリをワーカーのループ上で動かし、シグナルの処理は nuke-di に任せられます。
- `cleanup()` が処理中のリクエストを待つ時間である `shutdown_timeout` は、デフォルトで 60 秒です。`SHUTDOWN_GRACE_SECONDS` より短く設定してください。
- アプリ自身の `on_startup`、`on_cleanup`、`cleanup_ctx` も、クライアントが接続されている間に `setup()` と `cleanup()` の中でそのまま実行されます。

## <a id="websockets"></a>websockets

websockets 17.2 の asyncio 実装 `websockets.asyncio.server` で確認しています。`websockets.legacy` は 14.0 から非推奨です。

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
        for one in genre:
            await websocket.send(one)
            print(f"ask: {await websocket.recv(decode=True)} {one} books")
```

1 つ目のターミナル：

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

もう 1 つのターミナル：

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

- `serve()` を抜けると、開いているすべての接続が 1001（going away）でただちに閉じられます。gRPC や aiohttp と違い、処理中のメッセージは応答を失います。`classic -> 1` は数えられましたが、送信されませんでした。いずれにせよ WebSocket のクライアントは 1001 を受けると再接続するので、メッセージは再送しても安全なものにしてください。
- クライアントがクロージングハンドシェイクに応答するまでの待ち時間である `close_timeout` は、デフォルトで 10 秒です。これはデフォルトの猶予期間全体と同じ長さなので、短くしてください。

## <a id="apscheduler"></a>APScheduler

現行リリースの APScheduler 3.11.3 で確認しています。ジョブはクライアントのメソッドです。

```python
# app/scheduler.py
import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from nuke_di import Client, Shutdown, worker

from app.clients import Database


class Reports(Client):
    def __init__(self, db: Database) -> None:
        self.db = db
        self.running = asyncio.Lock()

    async def count_sci_fi(self) -> None:
        async with self.running:
            print("reports: counting")
            print(f"reports: {await self.db.count_books('sci-fi')} sci-fi books")


@worker
async def schedule(reports: Reports, shutdown: Shutdown) -> None:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(reports.count_sci_fi, "interval", seconds=2)
    scheduler.start()
    print("scheduler: started")
    await shutdown.wait()
    scheduler.pause()  # no new runs
    async with reports.running:  # the run in flight finishes
        # APScheduler 3 cancels the coroutine jobs still running here, whatever `wait` says
        scheduler.shutdown()
    print("scheduler: stopped")
```

2 回目の実行中に Ctrl+C を押した場合：

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

- `AsyncIOScheduler.shutdown()` はコルーチンのジョブを待てません。`wait=` の指定にかかわらず、実行中のジョブをキャンセルします。ロックがあれば、処理中の実行を最後まで終わらせられます。ワーカーはスケジューラーを一時停止し、実行がロックを解放したらそれを取得して、そこで初めてスケジューラーをシャットダウンします。ロックがないと、同じ Ctrl+C で実行が `count_books` の中の `asyncio.exceptions.CancelledError` で終わります。
- `start()` は実行中のイベントループを使うので、スケジューラーはインポート時ではなくワーカーの中で作成して起動します。ジョブを `DI.inject()` でバインドした関数ではなくクライアントのメソッドにするのは、ワーカーの中ではコンテナがすでに接続されていて、`inject()` は接続前にしか使えないからです。

### <a id="apscheduler-4"></a>APScheduler 4

APScheduler 4 はプレリリースで、4.0.0a6 で確認しています。API は一新されており、`AsyncScheduler` は非同期コンテキストマネージャーで、スケジュールは `await add_schedule()` で追加します。`Reports` はそのままで、インポートとワーカーは次のようになります。

```python
# app/scheduler.py, on APScheduler 4
from apscheduler import AsyncScheduler
from apscheduler.triggers.interval import IntervalTrigger


@worker
async def schedule(reports: Reports, shutdown: Shutdown) -> None:
    async with AsyncScheduler() as scheduler:
        await scheduler.add_schedule(reports.count_sci_fi, IntervalTrigger(seconds=2), id="count-sci-fi")
        await scheduler.start_in_background()
        print("scheduler: started")
        await shutdown.wait()
        async with reports.running:  # the run in flight finishes
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
```

APScheduler 4 は、インターバルのスケジュールをまずすぐに実行し、そのあと 2 秒ごとに実行します。その `stop()` も実行中のジョブを、出力に何も残さずにキャンセルするので、ロックは引き続き必要です。

## <a id="textual"></a>Textual

Textual 8.2.8 で確認しています。Textual のアプリはクライアントではありません。テストがフェイクを渡すのと同じように、ワーカーがアプリを組み立て、自分が受け取ったクライアントをアプリに渡します。

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

アプリはターミナルを占有します。ここでは 60 桁 × 8 行です。

```text
2 sci-fi books






 r Refresh  q Quit                              ▏^p palette
```

`q` でアプリを閉じると、プロセスは `0` で終了します。SIGTERM の場合は `exit_on_shutdown` 経由で閉じられ、`143` で終了します。

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
0
```

- `App.run()` は独自のイベントループを起動します。ワーカーの中では、`await app.run_async()` でアプリをワーカーのループ上で動かします。アプリが自分で閉じた場合は、`BackgroundTasks` が `exit_on_shutdown` をキャンセルします。
- Textual はターミナルのシグナルキーを無効にします。Ctrl+C は SIGINT ではなく、"Do you want to quit? Press ctrl+q to quit the app" と尋ねるキーになります。`TEXTUAL_ALLOW_SIGNALS=1` を設定すると Ctrl+C で SIGINT が送られ、アプリが閉じてプロセスは `130` で終了します。SIGTERM はどちらの場合も nuke-di に届きます。
- アプリの実行中は Textual が `print()` を横取りするので、ワーカーは `run_async()` が戻ってから出力します。Textual 自身のワーカーである `@work` と `run_worker()` はアプリの中で動くもので、`@worker` とは関係ありません。

アプリは `__init__` でクライアントを受け取るので、テストでは Textual の `run_test()` を使ってモックを渡します。

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

temporalio 1.34.0 と Temporal CLI 1.9.1 の開発サーバーで確認しています。I/O を行うのはアクティビティです。Temporal でアクティビティにデータベースを渡すには、アクティビティをメソッドとして持ち、ワーカーごとに一度だけ組み立てられるクラスを使います。このクラスがクライアントです。

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

Temporal への接続もクライアントです。ほかの[サードパーティのオブジェクト](../../adr/0005-third-party-objects-as-client-classes.md)と同じように、`connect()` の中で temporalio の `Client` を作成します。

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

ワークフローは専用のモジュールに置き、ワーカーとワークフローを開始するジョブの両方がそれをインポートします。

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

2 つ目のアクティビティの最中に停止し、もう一度起動したワーカー：

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

ワークフロー（別のターミナル）：

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

処理中のアクティビティは `graceful_shutdown_timeout` の範囲内で終わり、Temporal はその結果を保存しました。ワークフローがその結果を受け取る前にワーカーが停止したので `start` は待ち続け、次のワーカーがアクティビティを再実行せずにワークフローを完了させました。

- `graceful_shutdown_timeout` はデフォルトで 0 です。つまりワーカーがシャットダウンした瞬間に、処理中のアクティビティはキャンセルされます。`SHUTDOWN_GRACE_SECONDS` より短い値を設定してください。
- temporalio の `Client` は nuke-di の `Client` と同じ名前なので、`TemporalClient` としてインポートします。
- ワーカーには、解決されたインスタンスのバウンドメソッドとしてアクティビティを渡します（`activities=[activities.count_books]`）。ワークフローからは、クラスのメソッド `BookActivities.count_books` でアクティビティを指定します。

### <a id="workflows-never-take-clients"></a>ワークフローはクライアントを受け取らない

ワークフローは、Temporal が履歴からリプレイするコードです。どのワーカーでも、いつでも、サンドボックスの中で実行されるので、決定的でなければならず、I/O を行ってはいけません。したがって、ワークフローがクライアントを受け取ることはありません。`CountBooks` の `__init__` には引数がなく、Temporal がそれを組み立て、nuke-di がそれを目にすることはありません。データベースや HTTP API、キューにアクセスするものはすべてアクティビティです。ワークフローのモジュールが `BookActivities` をインポートするのはそのメソッドを指定するためだけで、`workflow.unsafe.imports_passed_through()` の下でインポートします。こうすると、サンドボックスはモジュールを改めてインポートせず、すでにインポート済みのものを使います。

テストでは、Temporal の時間をスキップするテストサーバー上でワークフローを実行し、アクティビティはモックを使って手で組み立てます。

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

初回の実行時にはテストサーバーがダウンロードされます。
