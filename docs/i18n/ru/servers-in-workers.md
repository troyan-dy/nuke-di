# <a id="servers-inside-a-worker"></a>Серверы внутри воркера

[English](../../guide/servers-in-workers.md) · **Русский** · [简体中文](../zh-CN/servers-in-workers.md) · [Español](../es/servers-in-workers.md) · [Português (Brasil)](../pt-BR/servers-in-workers.md) · [日本語](../ja/servers-in-workers.md) · [Polski](../pl/servers-in-workers.md)

← [Документация](../README.ru.md#documentation)

У grpc.aio, aiohttp, websockets, APScheduler, Textual и Temporal нет собственного внедрения зависимостей, поэтому
в nuke-di нет для них модуля, и он не нужен: сервер работает внутри [`@worker`](workers-and-jobs.md#your-first-worker),
а класс, методы которого служат его обработчиками, — это `Client`.

## <a id="the-pattern"></a>Схема

- **Обработчики — методы клиента.** Сервисер gRPC, класс представлений aiohttp, обработчик WebSocket, класс
  заданий по расписанию, активности Temporal: каждый принимает свои клиенты в `__init__`, а воркер получает его
  по аннотации типа. Он разрешается, и его клиенты подключаются до старта сервера; обработчик обращается к ним
  через `self`.
- **Сервером владеет воркер.** Он создаёт и запускает сервер, ждёт
  [`Shutdown`](workers-and-jobs.md#your-first-worker), останавливает сервер и возвращает управление; после этого
  клиенты отключаются. Код завершения, логи и хуки — те же, что у [любого воркера](workers-and-jobs.md#exit-codes).
- **Сервер останавливается в пределах grace period.** При остановке незавершённым вызовам дают доработать, и у
  каждого сервера для этого свой таймаут. Держите его меньше `SHUTDOWN_GRACE_SECONDS`: воркер, который всё ещё
  останавливается по истечении [grace period](workers-and-jobs.md#grace-period), отменяется, а вместе с ним и
  незавершённые вызовы.

| Сервер | Клиент | Чем останавливается | Его таймаут по умолчанию |
|---|---|---|---|
| [grpc.aio](#grpcaio) | Сервисер | `await server.stop(grace)` | `grace`, обязателен; `None` обрывает незавершённые вызовы |
| [aiohttp](#aiohttp) | Класс представлений | `await runner.cleanup()` | `shutdown_timeout`, 60 с |
| [websockets](#websockets) | Класс с обработчиком соединения | Выход из `async with serve(...)` | `close_timeout`, 10 с |
| [APScheduler](#apscheduler) | Класс заданий | `scheduler.shutdown()` | Таймаута нет: выполняющееся задание отменяется |
| [Textual](#textual) | Нет: `App` получает клиенты от воркера | `app.exit()` | Таймаута нет |
| [Temporal](#temporal) | Класс активностей | Выход из `async with Worker(...)` | `graceful_shutdown_timeout`, 0 |

Все рецепты используют общий модуль клиентов: базу данных, запрос к которой длится секунду, чтобы в момент
остановки процесса вызов ещё выполнялся.

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

Каждый рецепт посылает SIGTERM посреди вызова — так Kubernetes останавливает под.

## <a id="grpcaio"></a>grpc.aio

Проверено на grpcio 1.84.0.

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

Сервисер и есть клиент:

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

Джоба, которая его вызывает:

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

SIGTERM пришёл посреди второго вызова: вызов завершился и получил ответ, затем остановился сервер, затем
отключилась база данных.

- `server.stop(grace)` сразу отклоняет новые вызовы, даёт незавершённым `grace` секунд, а затем обрывает их.
  Держите `grace` меньше `SHUTDOWN_GRACE_SECONDS`, иначе nuke-di отменит воркер раньше.
- Запускайте `protoc` из корня проекта с `-I.`: тогда сгенерированный `books_pb2_grpc.py` импортирует
  `from app import books_pb2`, а `--pyi_out` даёт тайпчекеру классы сообщений.

## <a id="aiohttp"></a>aiohttp

Проверено на aiohttp 3.14.4. Обработчики — методы клиента, они добавляются в маршруты как связанные методы:

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

В одном терминале:

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

В другом:

```console
$ curl -s -w '\n' localhost:8080/books/sci-fi/count
{"genre": "sci-fi", "count": 2}
$ curl -s -w '\n' localhost:8080/books/classic/count & sleep 0.5; pkill -TERM -f app.aiohttp_server; wait
{"genre": "classic", "count": 1}
```

- `web.run_app()` нельзя запустить внутри воркера: он запускает собственный цикл событий и сам обрабатывает
  SIGINT и SIGTERM. `AppRunner` и `TCPSite` запускают то же приложение в цикле событий воркера и оставляют
  сигналы nuke-di.
- `shutdown_timeout` — сколько `cleanup()` ждёт незавершённые запросы — по умолчанию равен 60 секундам: задайте
  его меньше `SHUTDOWN_GRACE_SECONDS`.
- Собственные `on_startup`, `on_cleanup` и `cleanup_ctx` приложения по-прежнему выполняются — в `setup()` и
  `cleanup()`, — пока клиенты подключены.

## <a id="websockets"></a>websockets

Проверено на websockets 17.2, с реализацией на asyncio, `websockets.asyncio.server`; `websockets.legacy`
объявлен устаревшим с версии 14.0.

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

В одном терминале:

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

В другом:

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

- Выход из `serve()` сразу закрывает все открытые соединения с кодом 1001 (going away). В отличие от gRPC и
  aiohttp, сообщение, которое ещё обрабатывалось, остаётся без ответа: `classic -> 1` было посчитано, но так и
  не отправлено. Клиент WebSocket при 1001 всё равно переподключается, поэтому сделайте повторную отправку
  сообщения безопасной.
- `close_timeout` — сколько клиент может отвечать на закрывающее рукопожатие — по умолчанию равен 10 секундам,
  то есть всему grace period по умолчанию: уменьшите его.

## <a id="apscheduler"></a>APScheduler

Проверено на APScheduler 3.11.3, текущем релизе. Задания — методы клиента:

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

Ctrl+C во время второго запуска:

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

- `AsyncIOScheduler.shutdown()` не умеет ждать задание-корутину: он отменяет задания, которые ещё выполняются,
  что бы ни говорил `wait=`. Блокировка даёт текущему запуску доработать: воркер ставит планировщик на паузу,
  захватывает блокировку, как только запуск её отпустит, и только потом останавливает планировщик. Без блокировки
  тот же Ctrl+C обрывает запуск с `asyncio.exceptions.CancelledError` в `count_books`.
- `start()` берёт работающий цикл событий, поэтому планировщик создаётся и запускается внутри воркера, а не при
  импорте. Задание — метод клиента, а не функция, связанная через `DI.inject()`: внутри воркера контейнер уже
  подключён, а `inject()` работает только до подключения.

### <a id="apscheduler-4"></a>APScheduler 4

APScheduler 4 пока в пре-релизе, проверено на 4.0.0a6, и API у него новый: `AsyncScheduler` — асинхронный
контекстный менеджер, а расписание добавляется через `await add_schedule()`. `Reports` остаётся как есть;
импорты и воркер становятся такими:

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

APScheduler 4 запускает интервальное расписание сразу, а затем каждые 2 секунды. Его `stop()` тоже отменяет
выполняющееся задание, причём молча, без следа в выводе, поэтому блокировка остаётся.

## <a id="textual"></a>Textual

Проверено на Textual 8.2.8. Приложение Textual — не клиент: воркер создаёт его и передаёт ему полученные
клиенты, как тест передаёт подделки.

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

Приложение занимает терминал, здесь 60 столбцов на 8 строк:

```text
2 sci-fi books






 r Refresh  q Quit                              ▏^p palette
```

`q` закрывает его, и процесс завершается с кодом `0`; SIGTERM закрывает его через `exit_on_shutdown`, с кодом
`143`:

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
0
```

- `App.run()` запускает собственный цикл событий; внутри воркера `await app.run_async()` запускает приложение в
  цикле событий воркера. `BackgroundTasks` отменяет `exit_on_shutdown`, когда приложение закрылось само.
- Textual отключает сигнальные клавиши терминала: Ctrl+C — это клавиша, которая спрашивает «Do you want to quit?
  Press ctrl+q to quit the app», а не SIGINT. С `TEXTUAL_ALLOW_SIGNALS=1` Ctrl+C посылает SIGINT: приложение
  закрывается, и процесс завершается с кодом `130`. SIGTERM доходит до nuke-di в любом случае.
- Пока приложение работает, Textual перехватывает `print()`; воркер печатает, когда `run_async()` вернёт
  управление. Собственные воркеры Textual, `@work` и `run_worker()`, работают внутри приложения и не имеют
  ничего общего с `@worker`.

Приложение получает клиенты в `__init__`, поэтому тест передаёт ему мок и запускает его через `run_test()` из
Textual:

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

Проверено на temporalio 1.34.0 и dev-сервере из Temporal CLI 1.9.1. Ввод-вывод выполняют активности, и
способ, которым Temporal даёт им базу данных, — класс, методы которого и есть активности, создаваемый один раз
на воркер. Этот класс — клиент:

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

Подключение к Temporal — тоже клиент: он создаёт `Client` из temporalio в `connect()`, как любой
[сторонний объект](../../adr/0005-third-party-objects-as-client-classes.md):

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

Воркфлоу живёт в отдельном модуле, который импортируют и воркер, и джоба, которая его запускает:

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

Воркер, остановленный посреди второй активности и запущенный снова:

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

Воркфлоу, в другом терминале:

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

Незавершённая активность доработала в пределах `graceful_shutdown_timeout`, и Temporal сохранил её результат.
Воркер остановился раньше, чем воркфлоу забрал этот результат, поэтому `start` ждал; следующий воркер довёл
воркфлоу до конца, не запуская активность повторно.

- `graceful_shutdown_timeout` по умолчанию равен 0: незавершённые активности отменяются в тот же момент, когда
  воркер начинает остановку. Задайте его меньше `SHUTDOWN_GRACE_SECONDS`.
- `Client` из temporalio называется так же, как клиент nuke-di: импортируйте его как `TemporalClient`.
- Воркер получает активности как связанные методы разрешённого экземпляра, `activities=[activities.count_books]`;
  воркфлоу ссылается на них через метод класса, `BookActivities.count_books`.

### <a id="workflows-never-take-clients"></a>Воркфлоу никогда не принимает клиенты

Воркфлоу — это код, который Temporal воспроизводит по его истории, на любом воркере и в любой момент, внутри
песочницы: он должен быть детерминированным и не выполнять ввода-вывода. Поэтому воркфлоу никогда не принимает
клиенты. У `CountBooks` нет аргументов `__init__`, его создаёт Temporal, и nuke-di его никогда не видит; всё,
что обращается к базе данных, HTTP API или очереди, — это активность. Модуль воркфлоу импортирует
`BookActivities` только для того, чтобы ссылаться на его методы, под `workflow.unsafe.imports_passed_through()`,
поэтому песочница использует уже импортированный модуль, а не импортирует его заново.

Тест запускает воркфлоу на тестовом сервере Temporal с пропуском времени (time-skipping), а активности
создаются вручную вокруг мока:

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

При первом запуске тестовый сервер скачивается.
