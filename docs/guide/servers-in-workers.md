# Servers inside a worker

**English** · [Русский](../i18n/ru/servers-in-workers.md) · [简体中文](../i18n/zh-CN/servers-in-workers.md) · [Español](../i18n/es/servers-in-workers.md) · [Português (Brasil)](../i18n/pt-BR/servers-in-workers.md) · [日本語](../i18n/ja/servers-in-workers.md) · [Polski](../i18n/pl/servers-in-workers.md)

← [Documentation](../../README.md#documentation)

grpc.aio, aiohttp, websockets, APScheduler, Textual and Temporal have no dependency injection of their own, so
nuke-di has no module for them, and needs none: the server runs inside a [`@worker`](workers-and-jobs.md#your-first-worker),
and the class whose methods are its handlers is a `Client`.

## The pattern

- **The handlers are methods of a client.** A gRPC servicer, a class of aiohttp views, a WebSocket handler, a
  class of scheduled jobs, Temporal activities: each takes its clients in `__init__`, and the worker takes it by
  type hint. It is resolved, and its clients are connected, before the server starts; a handler reaches them
  through `self`.
- **The worker owns the server.** It builds and starts the server, waits for
  [`Shutdown`](workers-and-jobs.md#your-first-worker), stops the server and returns; then the clients disconnect.
  The exit code, the logs and the hooks are those of [every worker](workers-and-jobs.md#exit-codes).
- **The server stops within the grace period.** Stopping lets the calls in flight finish, and each server has
  its own timeout for that. Keep it below `SHUTDOWN_GRACE_SECONDS`: a worker still stopping after
  [the grace period](workers-and-jobs.md#grace-period) is cancelled, and the calls in flight with it.

| Server | The client | Stopped by | Its timeout, by default |
|---|---|---|---|
| [grpc.aio](#grpcaio) | The servicer | `await server.stop(grace)` | `grace`, required; `None` aborts the calls in flight |
| [aiohttp](#aiohttp) | A class of views | `await runner.cleanup()` | `shutdown_timeout`, 60 s |
| [websockets](#websockets) | A class with the connection handler | Leaving `async with serve(...)` | `close_timeout`, 10 s |
| [APScheduler](#apscheduler) | A class of jobs | `scheduler.shutdown()` | No timeout: a running job is cancelled |
| [Textual](#textual) | None: the `App` gets its clients from the worker | `app.exit()` | No timeout |
| [Temporal](#temporal) | A class of activities | Leaving `async with Worker(...)` | `graceful_shutdown_timeout`, 0 |

The recipes share one module of clients: a database whose query takes a second, so that a call is still in
flight when the process is stopped.

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

Each recipe sends SIGTERM in the middle of a call, the way Kubernetes stops a pod.

## grpc.aio

Checked with grpcio 1.84.0.

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

The servicer is the client:

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

A job to call it:

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

SIGTERM came in the middle of the second call: the call finished and got its answer, then the server stopped,
then the database disconnected.

- `server.stop(grace)` refuses new calls at once, gives the calls in flight `grace` seconds and then aborts
  them. Keep `grace` below `SHUTDOWN_GRACE_SECONDS`, or nuke-di cancels the worker first.
- Run `protoc` from the project root with `-I.`: the generated `books_pb2_grpc.py` then imports
  `from app import books_pb2`, and `--pyi_out` gives a type checker the message classes.

## aiohttp

Checked with aiohttp 3.14.4. The handlers are methods of a client, added to the routes as bound methods:

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

In one terminal:

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

In another:

```console
$ curl -s -w '\n' localhost:8080/books/sci-fi/count
{"genre": "sci-fi", "count": 2}
$ curl -s -w '\n' localhost:8080/books/classic/count & sleep 0.5; pkill -TERM -f app.aiohttp_server; wait
{"genre": "classic", "count": 1}
```

- `web.run_app()` cannot run inside a worker: it starts an event loop of its own and handles SIGINT and SIGTERM
  itself. `AppRunner` and `TCPSite` run the same app on the worker's loop and leave the signals to nuke-di.
- `shutdown_timeout`, how long `cleanup()` waits for the requests in flight, is 60 seconds by default: set it
  below `SHUTDOWN_GRACE_SECONDS`.
- The app's own `on_startup`, `on_cleanup` and `cleanup_ctx` still run, in `setup()` and `cleanup()`, while the
  clients are connected.

## websockets

Checked with websockets 17.2, on its asyncio implementation, `websockets.asyncio.server`; `websockets.legacy`
is deprecated since 14.0.

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

In one terminal:

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

In another:

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

- Leaving `serve()` closes every open connection at once, with 1001 (going away). Unlike gRPC and aiohttp, a
  message in flight loses its reply: `classic -> 1` was counted and never sent. A WebSocket client reconnects on
  1001 anyway, so make a message safe to send again.
- `close_timeout`, how long a client may take to answer the closing handshake, is 10 seconds by default, as long
  as the whole default grace period: lower it.

## APScheduler

Checked with APScheduler 3.11.3, the current release. The jobs are methods of a client:

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

Ctrl+C during the second run:

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

- `AsyncIOScheduler.shutdown()` cannot wait for a coroutine job: it cancels the jobs still running, whatever
  `wait=` says. The lock lets the run in flight finish: the worker pauses the scheduler, takes the lock once the
  run releases it, and only then shuts the scheduler down. Without the lock, the same Ctrl+C ends the run with
  `asyncio.exceptions.CancelledError` in `count_books`.
- `start()` takes the running event loop, so the scheduler is created and started inside the worker, not at
  import. A job is a method of a client rather than a function bound with `DI.inject()`: inside a worker the
  container is already connected, and `inject()` works only before it connects.

### APScheduler 4

APScheduler 4 is a pre-release, checked with 4.0.0a6, and its API is new: `AsyncScheduler` is an async context
manager, and a schedule is added with `await add_schedule()`. `Reports` stays as it is; the imports and the
worker become:

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

APScheduler 4 runs an interval schedule at once, then every 2 seconds. Its `stop()` cancels a running job as
well, without a word in the output, so the lock stays.

## Textual

Checked with Textual 8.2.8. A Textual app is not a client: the worker builds it and passes it the clients it
took, as a test passes fakes.

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

The app takes the terminal, here 60 columns by 8 lines:

```text
2 sci-fi books






 r Refresh  q Quit                              ▏^p palette
```

`q` closes it, and the process exits with `0`; SIGTERM closes it through `exit_on_shutdown`, with `143`:

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
0
```

- `App.run()` starts an event loop of its own; inside a worker, `await app.run_async()` runs the app on the
  worker's loop. `BackgroundTasks` cancels `exit_on_shutdown` when the app has closed by itself.
- Textual turns the terminal's signal keys off: Ctrl+C is a key that asks "Do you want to quit? Press ctrl+q
  to quit the app", not SIGINT. With `TEXTUAL_ALLOW_SIGNALS=1`, Ctrl+C sends SIGINT: the app closes and the
  process exits with `130`. SIGTERM reaches nuke-di either way.
- While the app runs, Textual captures `print()`; the worker prints once `run_async()` returns. Textual's own
  workers, `@work` and `run_worker()`, run inside the app and have nothing to do with `@worker`.

The app gets its clients in `__init__`, so a test passes a mock, with Textual's `run_test()`:

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

## Temporal

Checked with temporalio 1.34.0 and the dev server of the Temporal CLI 1.9.1. Activities do the I/O, and
Temporal's way to give them a database is a class whose methods are the activities, built once per worker. That
class is a client:

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

The connection to Temporal is a client too, one that creates temporalio's `Client` in `connect()`, as any
[third-party object](../adr/0005-third-party-objects-as-client-classes.md):

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

The workflow lives in a module of its own, which both the worker and the job that starts it import:

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

The worker, stopped in the middle of the second activity and started again:

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

The workflows, in another terminal:

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

The activity in flight finished within `graceful_shutdown_timeout`, and Temporal kept its result. The worker
stopped before the workflow took that result, so `start` waited; the next worker finished the workflow without
running the activity again.

- `graceful_shutdown_timeout` is 0 by default: the activities in flight are cancelled the moment the worker
  shuts down. Set it below `SHUTDOWN_GRACE_SECONDS`.
- temporalio's `Client` shares its name with nuke-di's: import it as `TemporalClient`.
- The worker gets the activities as bound methods of the resolved instance, `activities=[activities.count_books]`;
  a workflow names them by the method of the class, `BookActivities.count_books`.

### Workflows never take clients

A workflow is code that Temporal replays from its history, on any worker and at any time, inside a sandbox: it
must be deterministic and do no I/O. So a workflow never takes a client. `CountBooks` has no `__init__`
arguments, Temporal builds it, and nuke-di never sees it; whatever reaches a database, an HTTP API or a queue is
an activity. The workflow module imports `BookActivities` only to name its methods, under
`workflow.unsafe.imports_passed_through()`, so the sandbox uses the module already imported instead of importing
it again.

A test runs the workflow on Temporal's time-skipping test server, with the activities built by hand around a
mock:

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

The first run downloads the test server.
