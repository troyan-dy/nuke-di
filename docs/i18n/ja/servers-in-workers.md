# <a id="servers-inside-a-worker"></a>ワーカーの中で動くサーバー

[English](../../guide/servers-in-workers.md) · [Русский](../ru/servers-in-workers.md) · [简体中文](../zh-CN/servers-in-workers.md) · [Español](../es/servers-in-workers.md) · [Português (Brasil)](../pt-BR/servers-in-workers.md) · **日本語** · [Polski](../pl/servers-in-workers.md)

← [ドキュメント](../README.ja.md#documentation)

grpc.aio、aiohttp、websockets、APScheduler、Textual、Temporal には独自の依存性注入がありません。そのため nuke-di にはこれらのためのモジュールがなく、必要もありません。サーバーは [`@worker`](workers-and-jobs.md#your-first-worker) の中で動き、そのハンドラーをメソッドとして持つクラスが `Client` になります。

## <a id="the-pattern"></a>パターン

- **ハンドラーはクライアントのメソッドです。** gRPC のサービサー、aiohttp のビューをまとめたクラス、WebSocket のハンドラー、APScheduler のジョブのクラス、Temporal のアクティビティ。どれも `__init__` で自分のクライアントを受け取り、ワーカーはそれを型ヒントで受け取ります。サーバーが起動する前にそれが解決され、そのクライアントが接続されるので、ハンドラーは `self` を通じてクライアントにアクセスできます。
- **サーバーはワーカーのものです。** ワーカーがサーバーを組み立てて起動し、[`Shutdown`](workers-and-jobs.md#your-first-worker) を待ち、サーバーを止めてから戻ります。そのあとクライアントが切断されます。終了コード、ログ、フックは[ほかのワーカー](workers-and-jobs.md#exit-codes)と同じです。
- **サーバーは猶予期間内に余裕をもって停止します。** 停止の際には処理中の呼び出しが終わるのを待ちますが、その待ち時間の上限はサーバーごとに異なります。これはプロセスが実際に使う `SHUTDOWN_GRACE_SECONDS` より短くしてください。こちらは環境変数から来る値で、レシピの上限はコードに書かれています。[猶予期間](workers-and-jobs.md#grace-period)を過ぎてもまだ停止中のワーカーはキャンセルされますが、ハンドラーはサーバー自身のタスクで動いているので、ワーカーをキャンセルしてもハンドラーは止まりません。切断中のクライアントに対してそのまま動き続けます。gRPC と APScheduler のレシピは `finally` の中でハンドラーを打ち切りますが、aiohttp、websockets、Temporal では上限だけが頼りです。
- **停止全体には予算があります。** プロセスの停止には最大で `SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × 最も長い依存関係の連鎖` かかります（[猶予期間](workers-and-jobs.md#grace-period)）。Pod の `terminationGracePeriodSeconds` はそれより長くしてください（[Kubernetes](workers-and-jobs.md#running-in-kubernetes)）。

| サーバー | クライアント | 停止の方法 | デフォルトの待ち時間 |
|---|---|---|---|
| [grpc.aio](#grpcaio) | サービサー | `await server.stop(grace)` | `grace`、必須。`None` だと処理中の呼び出しを打ち切る |
| [aiohttp](#aiohttp) | ビューのクラス | `await runner.cleanup()` | 最大 2 × `shutdown_timeout`、それぞれ 60 秒 |
| [websockets](#websockets) | 接続ハンドラーを持つクラス | `async with serve(...)` を抜ける | `close_timeout`、10 秒、クロージングハンドシェイクのみ。ハンドラーには上限がない |
| [APScheduler](#apscheduler) | APScheduler のジョブのクラス | `scheduler.shutdown()` | 待たない：実行中の APScheduler のジョブはキャンセルされる |
| [Textual](#textual) | なし：`App` はワーカーからクライアントを受け取る | `app.exit()` | 待たない |
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

どのレシピでも、Kubernetes が Pod を止めるときと同じように、呼び出しの最中に SIGTERM を送ります。`app/grpc_ask.py` のようにサーバーを呼び出すプログラムは、その場で接続を開きます。これらはデモ用の使い捨ての呼び出し側で、アプリの一部ではありません。アプリ自身が保持する接続はクライアントです。[後述](#temporal)の Temporal への接続がそうです。

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

- `server.stop(grace)` は新しい呼び出しをただちに拒否し、処理中の呼び出しに `grace` 秒の猶予を与えてから打ち切ります。`grace` は `SHUTDOWN_GRACE_SECONDS` より短くしてください。
- `protoc` はプロジェクトのルートから `-I.` を付けて実行します。こうすると生成される `books_pb2_grpc.py` は `from app import books_pb2` でインポートするようになります。`--pyi_out` はメッセージに型を付けます。mypy にはさらに `pip install types-grpcio` が必要で、`--strict` では mypy-protobuf が生成する `books_pb2_grpc.py` のスタブも必要です：`--mypy_grpc_out=.`。

`finally` は、猶予期間が終わってもワーカーがまだ停止中の場合に備えるものです。nuke-di がワーカーをキャンセルし、`server.stop(None)` がクライアントの切断より前に処理中の呼び出しを打ち切ります。猶予期間が呼び出しより短い場合：

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

呼び出しは打ち切られ、呼び出し側は `UNAVAILABLE` を受け取りました。切断されたデータベースに対して処理が続くことはありません。

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
- `cleanup()` は処理中のリクエストを最大 `shutdown_timeout` だけ待ち、そのあとリクエストをキャンセルして、さらに最大 `shutdown_timeout` 待ちます。つまり最大でタイムアウトの 2 倍で、タイムアウトはデフォルトで 60 秒です。タイムアウトの 2 倍を `SHUTDOWN_GRACE_SECONDS` より短くしてください。レシピの 4 秒なら、その 2 倍はデフォルトの 10 秒より短くなります。ここでは `finally` は役に立ちません。nuke-di のキャンセルは `cleanup()` の中で起き、処理中のハンドラーは切断中のクライアントに対して動き続けます。
- `on_shutdown` のコールバックは `cleanup()` の最初、処理中のリクエストを待つ前に実行されます。長く続く WebSocket や server-sent events の接続はここで閉じます。`on_startup`、`on_cleanup`、`cleanup_ctx` は、クライアントが接続されている間に `setup()` と `cleanup()` の中で実行されます。

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
        for name in genre:
            await websocket.send(name)
            print(f"ask: {await websocket.recv(decode=True)} {name} books")
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

- `serve()` を抜けると、開いているすべての接続が 1001（going away）でただちに閉じられます。gRPC や aiohttp と違い、処理中のメッセージは応答を失います。`classic -> 1` は数えられましたが、送信されませんでした。上の呼び出し側は 1001 で失敗します。実際のクライアントは、たとえば `async for websocket in connect(...)` で 1001 を受けたら再接続し、再送しても安全なメッセージを送るべきです。
- `close_timeout` はデフォルトで 10 秒で、各クライアントとのクロージングハンドシェイクだけを制限します。それでも短くしてください。デフォルト値はデフォルトの猶予期間全体と同じ長さです。ハンドラーには何の上限もありません。`serve()` を抜けるときはすべてのハンドラーが戻るのを待つので、止まったままのハンドラーがあるとワーカーは猶予期間を超えて残り、ワーカーがキャンセルされたあとは切断中のクライアントに対して動き続けます。

## <a id="apscheduler"></a>APScheduler

現行リリースの APScheduler 3.11.3 で確認しています。APScheduler のジョブはクライアントのメソッドです。

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

- `AsyncIOScheduler.shutdown()` はコルーチンの APScheduler のジョブを待てません。イベントループ上に停止を予約するだけで、その停止は `wait=` の指定にかかわらず、実行中のジョブをキャンセルします。ワーカーはスケジューラーを一時停止して新しい実行が始まらないようにし、`reports.between_runs()` の中で処理中の実行を待ちます。そこで初めてスケジューラーをシャットダウンします。この待機がないと、同じ Ctrl+C で実行が `count_books` の中の `asyncio.exceptions.CancelledError` で終わります。
- `finally` は、ワーカーがキャンセルされた場合にもスケジューラーをシャットダウンします。たとえば猶予期間より長い実行があった場合です。実行はデータベースが切断される前にキャンセルされ、切断中のデータベースに対して動き続けることはありません。`await asyncio.sleep(0)` によって、予約された停止がワーカーが戻る前に実行されます。
- スケジューラーは専用のクライアントではなくワーカーの中に置きます。スケジューラーのクライアントは、それを解決するすべてのコンテナで起動してしまいます。Web アプリのコンテナも例外ではありません。また、停止の順序、つまり処理中の実行が先でスケジューラーがあと、という順序を決めるのはワーカーです。APScheduler のジョブはクライアントのメソッドなので、`self` を通じてデータベースにアクセスし、`between_runs()` のロックを共有します。`DI.inject()` でバインドしたモジュールレベルの関数を `add_job()` に渡す方法も使えますが、`inject()` はワーカーが起動する前に呼ぶ必要があります。ワーカーの中ではコンテナが接続済みで、`inject()` は失敗します。

### <a id="apscheduler-4"></a>APScheduler 4

APScheduler 4 はアルファ版で、4.0.0a6 で確認しています。API は 4.0 までにまだ変わる可能性があります。`AsyncScheduler` は非同期コンテキストマネージャーで、スケジュールは `await add_schedule()` で追加します。`Reports` はそのままで、インポートとワーカーは次のようになります。

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

APScheduler 4 は、インターバルのスケジュールをまずすぐに実行し、そのあと 2 秒ごとに実行します。その `stop()` も実行中の APScheduler のジョブを、出力に何も残さずにキャンセルするので、処理中の実行を待つ処理は引き続き必要です。

## <a id="textual"></a>Textual

Textual 8.2.8 で確認しています。Textual のアプリはクライアントではありません。テストがモックを渡すのと同じように、ワーカーがアプリを組み立て、自分が受け取ったクライアントをアプリに渡します。

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

`q` でアプリを閉じると、プロセスは `0` で終了します。

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
0
```

SIGTERM（たとえば別のターミナルから `pkill -TERM -f app.tui` を実行）の場合は、`exit_on_shutdown` 経由で閉じられます。

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
143
```

- `App.run()` は独自のイベントループを起動します。ワーカーの中では、`await app.run_async()` でアプリをワーカーのループ上で動かします。アプリが自分で閉じた場合、`exit_on_shutdown` はまだ待機中です。`BackgroundTasks` が切断されるとき、つまりワーカーが戻ったあとに、それをキャンセルします。
- Textual はターミナルのシグナルキーを無効にします。Ctrl+C は SIGINT ではなく、"Do you want to quit? Press ctrl+q to quit the app" と尋ねるキーになります。`TEXTUAL_ALLOW_SIGNALS=1` を設定すると Ctrl+C で SIGINT が送られ、アプリが閉じてプロセスは `130` で終了します。SIGTERM はどちらの場合も nuke-di に届きます。
- アプリの実行中は Textual が `print()` を横取りするので、ワーカーは `run_async()` が戻ってから出力します。Textual 自身のワーカーである `@work` と `run_worker()` はアプリの中で動くもので、`@worker` とは関係ありません。

アプリは `__init__` でクライアントを受け取るので、テストでは Textual の `run_test()` を使ってモックを渡します。[テスト](testing.md)と同じく、pytest-asyncio で `asyncio_mode = auto` を使います。

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

temporalio 1.34.0 と Temporal CLI 1.9.1 の開発サーバーで確認しています。I/O を行うのはアクティビティです。Temporal でアクティビティにデータベースを渡すには、アクティビティをメソッドとして持ち、Temporal のワーカーごとに一度だけ組み立てられるクラスを使います。このクラスがクライアントです。

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

処理中のアクティビティは `graceful_shutdown_timeout` の範囲内で終わり、Temporal はその結果を保存しました。ワークフローがその結果を受け取る前にワーカーが停止したので `start` は待ち続け、次のワーカープロセスがアクティビティを再実行せずにワークフローを完了させました。

- `graceful_shutdown_timeout` はデフォルトで 0 です。つまりワーカーがシャットダウンした瞬間に、処理中のアクティビティはキャンセルされます。`SHUTDOWN_GRACE_SECONDS` より十分に短い値を設定してください。`async with Worker(...)` を抜ける処理がまだシャットダウン中のうちに nuke-di がワーカーをキャンセルすると、アクティビティは切断中のクライアントに対して動き続けます。
- temporalio の `Client` は nuke-di の `Client` と同じ名前なので、`TemporalClient` としてインポートします。
- Temporal の `Worker` には、解決されたインスタンスのバウンドメソッドとしてアクティビティを渡します（`activities=[activities.count_books]`）。ワークフローからは、クラスのメソッド `BookActivities.count_books` でアクティビティを指定します。

### <a id="workflows-never-take-clients"></a>ワークフローはクライアントを受け取らない

ワークフローは、Temporal が履歴からリプレイするコードです。どのワーカーでも、いつでも、サンドボックスの中で実行されるので、決定的でなければならず、I/O を行ってはいけません。したがって、ワークフローがクライアントを受け取ることはありません。`CountBooks` の `__init__` には引数がなく、Temporal がそれを組み立て、nuke-di がそれを目にすることはありません。データベースや HTTP API、キューにアクセスするものはすべてアクティビティです。ワークフローのモジュールが `BookActivities` をインポートするのはそのメソッドを指定するためだけで、`workflow.unsafe.imports_passed_through()` の下でインポートします。こうすると、サンドボックスはモジュールを改めてインポートせず、すでにインポート済みのものを使います。

テストでは、Temporal の時間をスキップするテストサーバー上でワークフローを実行し、アクティビティはモックを使って手で組み立てます。[テスト](testing.md)と同じく、pytest-asyncio で `asyncio_mode = auto` を使います。

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
