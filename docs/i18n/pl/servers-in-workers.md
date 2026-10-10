# <a id="servers-inside-a-worker"></a>Serwery wewnątrz workera

[English](../../guide/servers-in-workers.md) · [Русский](../ru/servers-in-workers.md) · [简体中文](../zh-CN/servers-in-workers.md) · [Español](../es/servers-in-workers.md) · [Português (Brasil)](../pt-BR/servers-in-workers.md) · [日本語](../ja/servers-in-workers.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

grpc.aio, aiohttp, websockets, APScheduler, Textual i Temporal nie mają własnego wstrzykiwania zależności, więc
nuke-di nie ma dla nich modułu i żadnego nie potrzebuje: serwer działa wewnątrz [`@worker`](workers-and-jobs.md#your-first-worker),
a klasa, której metody są jego handlerami, jest `Client`.

## <a id="the-pattern"></a>Wzorzec

- **Handlery są metodami klienta.** Servicer gRPC, klasa widoków aiohttp, handler WebSocket, klasa zadań
  harmonogramu, aktywności Temporal: każda przyjmuje swoich klientów w `__init__`, a worker przyjmuje ją po
  adnotacji typu. Zostaje rozwiązana, a jej klienci połączeni, zanim serwer wystartuje; handler sięga po nich
  przez `self`.
- **Serwer należy do workera.** Worker buduje i uruchamia serwer, czeka na
  [`Shutdown`](workers-and-jobs.md#your-first-worker), zatrzymuje serwer i wraca; potem klienci się rozłączają.
  Kod wyjścia, logi i hooki są takie same jak u [każdego workera](workers-and-jobs.md#exit-codes).
- **Serwer zatrzymuje się w okresie karencji.** Zatrzymanie pozwala dokończyć trwające wywołania, a każdy serwer
  ma na to własny limit czasu. Trzymaj go poniżej `SHUTDOWN_GRACE_SECONDS`: worker, który wciąż się zatrzymuje po
  [okresie karencji](workers-and-jobs.md#grace-period), zostaje anulowany, a razem z nim trwające wywołania.

| Serwer | Klient | Zatrzymuje go | Jego limit czasu, domyślnie |
|---|---|---|---|
| [grpc.aio](#grpcaio) | Servicer | `await server.stop(grace)` | `grace`, wymagany; `None` przerywa trwające wywołania |
| [aiohttp](#aiohttp) | Klasa widoków | `await runner.cleanup()` | `shutdown_timeout`, 60 s |
| [websockets](#websockets) | Klasa z handlerem połączenia | Wyjście z `async with serve(...)` | `close_timeout`, 10 s |
| [APScheduler](#apscheduler) | Klasa zadań | `scheduler.shutdown()` | Brak limitu: trwające zadanie jest anulowane |
| [Textual](#textual) | Brak: `App` dostaje klientów od workera | `app.exit()` | Brak limitu |
| [Temporal](#temporal) | Klasa aktywności | Wyjście z `async with Worker(...)` | `graceful_shutdown_timeout`, 0 |

Przepisy dzielą jeden moduł klientów: bazę danych, której zapytanie trwa sekundę, tak aby w chwili zatrzymania
procesu jakieś wywołanie wciąż trwało.

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

Każdy przepis wysyła SIGTERM w trakcie wywołania, tak jak Kubernetes zatrzymuje pod.

## <a id="grpcaio"></a>grpc.aio

Sprawdzone z grpcio 1.84.0.

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

Servicer jest klientem:

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

Job, który go wywołuje:

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

SIGTERM przyszedł w trakcie drugiego wywołania: wywołanie się skończyło i dostało odpowiedź, potem serwer się
zatrzymał, a potem baza danych się rozłączyła.

- `server.stop(grace)` od razu odrzuca nowe wywołania, daje trwającym `grace` sekund, a potem je przerywa.
  Trzymaj `grace` poniżej `SHUTDOWN_GRACE_SECONDS`, inaczej nuke-di anuluje workera wcześniej.
- Uruchamiaj `protoc` z katalogu głównego projektu z `-I.`: wygenerowany `books_pb2_grpc.py` importuje wtedy
  `from app import books_pb2`, a `--pyi_out` daje narzędziu do sprawdzania typów klasy komunikatów.

## <a id="aiohttp"></a>aiohttp

Sprawdzone z aiohttp 3.14.4. Handlery są metodami klienta, dodanymi do tras jako metody związane:

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

W jednym terminalu:

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

W drugim:

```console
$ curl -s -w '\n' localhost:8080/books/sci-fi/count
{"genre": "sci-fi", "count": 2}
$ curl -s -w '\n' localhost:8080/books/classic/count & sleep 0.5; pkill -TERM -f app.aiohttp_server; wait
{"genre": "classic", "count": 1}
```

- `web.run_app()` nie może działać wewnątrz workera: uruchamia własną pętlę zdarzeń i sam obsługuje SIGINT
  i SIGTERM. `AppRunner` i `TCPSite` uruchamiają tę samą aplikację na pętli workera, a sygnały zostawiają nuke-di.
- `shutdown_timeout`, czyli jak długo `cleanup()` czeka na trwające żądania, domyślnie wynosi 60 sekund: ustaw
  go poniżej `SHUTDOWN_GRACE_SECONDS`.
- Własne `on_startup`, `on_cleanup` i `cleanup_ctx` aplikacji nadal działają, w `setup()` i `cleanup()`, gdy
  klienci są połączeni.

## <a id="websockets"></a>websockets

Sprawdzone z websockets 17.2, na jego implementacji asyncio, `websockets.asyncio.server`; `websockets.legacy`
jest przestarzałe od wersji 14.0.

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

W jednym terminalu:

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

W drugim:

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

- Wyjście z `serve()` od razu zamyka każde otwarte połączenie z kodem 1001 (going away). Inaczej niż w gRPC
  i aiohttp, trwający komunikat traci odpowiedź: `classic -> 1` zostało policzone, ale nigdy niewysłane. Klient
  WebSocket i tak łączy się ponownie po 1001, więc zadbaj, by komunikat można było bezpiecznie wysłać jeszcze raz.
- `close_timeout`, czyli jak długo klient może odpowiadać na zamykający handshake, domyślnie wynosi 10 sekund,
  tyle co cały domyślny okres karencji: obniż go.

## <a id="apscheduler"></a>APScheduler

Sprawdzone z APScheduler 3.11.3, bieżącym wydaniem. Zadania są metodami klienta:

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

Ctrl+C w trakcie drugiego uruchomienia:

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

- `AsyncIOScheduler.shutdown()` nie potrafi poczekać na zadanie-korutynę: anuluje zadania, które wciąż trwają,
  niezależnie od `wait=`. Blokada pozwala dokończyć trwające uruchomienie: worker wstrzymuje harmonogram, bierze
  blokadę, gdy uruchomienie ją zwolni, i dopiero wtedy wyłącza harmonogram. Bez blokady to samo Ctrl+C kończy
  uruchomienie wyjątkiem `asyncio.exceptions.CancelledError` w `count_books`.
- `start()` bierze działającą pętlę zdarzeń, więc harmonogram tworzy się i uruchamia wewnątrz workera, a nie przy
  imporcie. Zadanie jest metodą klienta, a nie funkcją związaną przez `DI.inject()`: wewnątrz workera kontener
  jest już połączony, a `inject()` działa tylko przed połączeniem.

### <a id="apscheduler-4"></a>APScheduler 4

APScheduler 4 to wersja przedpremierowa, sprawdzona z 4.0.0a6, i ma nowe API: `AsyncScheduler` jest
asynchronicznym menedżerem kontekstu, a harmonogram dodaje się przez `await add_schedule()`. `Reports` zostaje bez
zmian; importy i worker wyglądają tak:

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

APScheduler 4 uruchamia harmonogram interwałowy od razu, a potem co 2 sekundy. Jego `stop()` też anuluje trwające
zadanie, bez słowa w wyjściu, więc blokada zostaje.

## <a id="textual"></a>Textual

Sprawdzone z Textual 8.2.8. Aplikacja Textual nie jest klientem: worker ją buduje i przekazuje jej klientów,
których sam przyjął, tak jak test przekazuje atrapy.

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

Aplikacja zajmuje terminal, tutaj 60 kolumn na 8 wierszy:

```text
2 sci-fi books






 r Refresh  q Quit                              ▏^p palette
```

`q` ją zamyka, a proces kończy się kodem `0`; SIGTERM zamyka ją przez `exit_on_shutdown`, z kodem `143`:

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
0
```

- `App.run()` uruchamia własną pętlę zdarzeń; wewnątrz workera `await app.run_async()` uruchamia aplikację na
  pętli workera. `BackgroundTasks` anuluje `exit_on_shutdown`, gdy aplikacja zamknęła się sama.
- Textual wyłącza klawisze sygnałów terminala: Ctrl+C to klawisz, który pyta "Do you want to quit? Press ctrl+q
  to quit the app", a nie SIGINT. Z `TEXTUAL_ALLOW_SIGNALS=1` Ctrl+C wysyła SIGINT: aplikacja się zamyka, a proces
  kończy się kodem `130`. SIGTERM dociera do nuke-di w każdym przypadku.
- Gdy aplikacja działa, Textual przechwytuje `print()`; worker wypisuje dopiero po powrocie z `run_async()`.
  Własne workery Textual, `@work` i `run_worker()`, działają wewnątrz aplikacji i nie mają nic wspólnego
  z `@worker`.

Aplikacja dostaje klientów w `__init__`, więc test przekazuje atrapę, z `run_test()` z Textual:

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

Sprawdzone z temporalio 1.34.0 i serwerem deweloperskim Temporal CLI 1.9.1. I/O wykonują aktywności, a sposób
Temporal na danie im bazy danych to klasa, której metody są aktywnościami, budowana raz na worker. Ta klasa jest
klientem:

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

Połączenie z Temporal też jest klientem, takim, który tworzy `Client` z temporalio w `connect()`, jak każdy
[obiekt zewnętrzny](../../adr/0005-third-party-objects-as-client-classes.md):

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

Workflow ma własny moduł, który importują zarówno worker, jak i job, który go uruchamia:

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

Worker, zatrzymany w trakcie drugiej aktywności i uruchomiony ponownie:

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

Workflowy, w drugim terminalu:

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

Trwająca aktywność skończyła się w `graceful_shutdown_timeout`, a Temporal zachował jej wynik. Worker zatrzymał
się, zanim workflow odebrał ten wynik, więc `start` czekał; następny worker dokończył workflow bez ponownego
uruchamiania aktywności.

- `graceful_shutdown_timeout` domyślnie wynosi 0: trwające aktywności są anulowane w chwili, gdy worker się
  wyłącza. Ustaw go poniżej `SHUTDOWN_GRACE_SECONDS`.
- `Client` z temporalio ma tę samą nazwę co `Client` z nuke-di: importuj go jako `TemporalClient`.
- Worker dostaje aktywności jako metody związane rozwiązanej instancji, `activities=[activities.count_books]`;
  workflow nazywa je metodą klasy, `BookActivities.count_books`.

### <a id="workflows-never-take-clients"></a>Workflowy nigdy nie przyjmują klientów

Workflow to kod, który Temporal odtwarza z jego historii, na dowolnym workerze i w dowolnej chwili, wewnątrz
piaskownicy: musi być deterministyczny i nie wykonywać I/O. Dlatego workflow nigdy nie przyjmuje klienta.
`CountBooks` nie ma argumentów `__init__`, buduje go Temporal, a nuke-di nigdy go nie widzi; wszystko, co sięga do
bazy danych, HTTP API czy kolejki, jest aktywnością. Moduł workflow importuje `BookActivities` tylko po to, by
nazwać jego metody, pod `workflow.unsafe.imports_passed_through()`, więc piaskownica używa już zaimportowanego
modułu, zamiast importować go ponownie.

Test uruchamia workflow na testowym serwerze Temporal z przeskakiwaniem czasu, z aktywnościami zbudowanymi ręcznie
wokół atrapy:

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

Pierwsze uruchomienie pobiera serwer testowy.
