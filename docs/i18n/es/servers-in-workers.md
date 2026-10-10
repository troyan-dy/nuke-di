# <a id="servers-inside-a-worker"></a>Servidores dentro de un worker

[English](../../guide/servers-in-workers.md) · [Русский](../ru/servers-in-workers.md) · [简体中文](../zh-CN/servers-in-workers.md) · **Español** · [Português (Brasil)](../pt-BR/servers-in-workers.md) · [日本語](../ja/servers-in-workers.md) · [Polski](../pl/servers-in-workers.md)

← [Documentación](../README.es.md#documentation)

grpc.aio, aiohttp, websockets, APScheduler, Textual y Temporal no tienen inyección de dependencias propia, así
que nuke-di no tiene ningún módulo para ellos, ni le hace falta: el servidor se ejecuta dentro de un
[`@worker`](workers-and-jobs.md#your-first-worker), y la clase cuyos métodos son sus handlers es un `Client`.

## <a id="the-pattern"></a>El patrón

- **Los handlers son métodos de un cliente.** Un servicer de gRPC, una clase de vistas de aiohttp, un handler de
  WebSocket, una clase de tareas de APScheduler, las activities de Temporal: cada uno recibe sus clientes en
  `__init__`, y el worker lo recibe por su type hint. Se resuelve, y sus clientes se conectan, antes de que
  arranque el servidor; un handler llega a ellos a través de `self`.
- **El worker es dueño del servidor.** Lo construye y lo arranca, espera a
  [`Shutdown`](workers-and-jobs.md#your-first-worker), detiene el servidor y retorna; después, los clientes se
  desconectan. El código de salida, los logs y los hooks son los de [cualquier worker](workers-and-jobs.md#exit-codes).
- **El servidor se detiene holgadamente dentro del periodo de gracia.** Al detenerse deja terminar las llamadas
  en curso, y cada servidor tiene su propio límite para ello. Mantenlo por debajo del `SHUTDOWN_GRACE_SECONDS`
  con el que se ejecuta el proceso: ese viene del entorno, mientras que las recetas escriben sus límites en el
  código. Un worker que sigue deteniéndose pasado [el periodo de gracia](workers-and-jobs.md#grace-period) se
  cancela, pero los handlers se ejecutan en las tareas propias del servidor, así que cancelar el worker no los
  detiene: siguen adelante contra clientes que se están desconectando. Las recetas de gRPC y APScheduler los
  abortan en un `finally`; para aiohttp, websockets y Temporal el límite es la única protección.
- **Toda la parada tiene un presupuesto.** Un proceso se detiene en hasta
  `SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × la cadena de dependencias más larga`
  ([periodo de gracia](workers-and-jobs.md#grace-period)); dale al pod un `terminationGracePeriodSeconds` por
  encima de eso ([Kubernetes](workers-and-jobs.md#running-in-kubernetes)).

| Servidor | El cliente | Se detiene con | Cuánto espera, por defecto |
|---|---|---|---|
| [grpc.aio](#grpcaio) | El servicer | `await server.stop(grace)` | `grace`, obligatorio; `None` aborta las llamadas en curso |
| [aiohttp](#aiohttp) | Una clase de vistas | `await runner.cleanup()` | Hasta 2 × `shutdown_timeout`, 60 s cada uno |
| [websockets](#websockets) | Una clase con el handler de la conexión | Salir de `async with serve(...)` | `close_timeout`, 10 s, solo para el handshake de cierre; los handlers no tienen límite |
| [APScheduler](#apscheduler) | Una clase de tareas de APScheduler | `scheduler.shutdown()` | Nada: una tarea de APScheduler en ejecución se cancela |
| [Textual](#textual) | Ninguno: la `App` recibe sus clientes del worker | `app.exit()` | Nada |
| [Temporal](#temporal) | Una clase de activities | Salir de `async with Worker(...)` | `graceful_shutdown_timeout`, 0 |

Las recetas comparten un mismo módulo de clientes: una base de datos cuya consulta tarda un segundo, para que
haya una llamada todavía en curso cuando se detiene el proceso.

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

Cada receta envía SIGTERM en medio de una llamada, igual que Kubernetes detiene un pod. Los programas que llaman
a los servidores, como `app/grpc_ask.py`, abren su conexión ahí mismo: son llamadores desechables para la demo,
no parte de la app. Una conexión que mantiene la propia app es un cliente, como la de Temporal [más
abajo](#temporal).

## <a id="grpcaio"></a>grpc.aio

Comprobado con grpcio 1.84.0.

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

El servicer es el cliente:

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

Un job para llamarlo:

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

SIGTERM llegó en medio de la segunda llamada: la llamada terminó y recibió su respuesta, luego el servidor se
detuvo y después la base de datos se desconectó.

- `server.stop(grace)` rechaza las llamadas nuevas de inmediato, da a las llamadas en curso `grace` segundos y
  después las aborta. Mantén `grace` por debajo de `SHUTDOWN_GRACE_SECONDS`.
- Ejecuta `protoc` desde la raíz del proyecto con `-I.`: así el `books_pb2_grpc.py` generado importa
  `from app import books_pb2`. `--pyi_out` tipa los mensajes; mypy también necesita `pip install types-grpcio`,
  y con `--strict`, los stubs de `books_pb2_grpc.py` de mypy-protobuf: `--mypy_grpc_out=.`.

El `finally` cubre un worker que sigue deteniéndose cuando termina el periodo de gracia: nuke-di lo cancela, y
`server.stop(None)` aborta las llamadas en curso antes de que los clientes se desconecten. Con un periodo de
gracia más corto que la llamada:

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

La llamada se abortó, y el llamador recibió `UNAVAILABLE`, en lugar de seguir adelante contra una base de datos
desconectada.

## <a id="aiohttp"></a>aiohttp

Comprobado con aiohttp 3.14.4. Los handlers son métodos de un cliente, que se añaden a las rutas como métodos
ligados:

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

En una terminal:

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

En otra:

```console
$ curl -s -w '\n' localhost:8080/books/sci-fi/count
{"genre": "sci-fi", "count": 2}
$ curl -s -w '\n' localhost:8080/books/classic/count & sleep 0.5; pkill -TERM -f app.aiohttp_server; wait
{"genre": "classic", "count": 1}
```

- `web.run_app()` no puede ejecutarse dentro de un worker: arranca su propio bucle de eventos y maneja SIGINT y
  SIGTERM por su cuenta. `AppRunner` y `TCPSite` ejecutan la misma app en el bucle del worker y dejan las señales
  a nuke-di.
- `cleanup()` espera hasta `shutdown_timeout` a una petición en curso, luego la cancela y vuelve a esperar hasta
  `shutdown_timeout`: hasta el doble del timeout, que es de 60 segundos por defecto. Mantén el doble del timeout
  por debajo de `SHUTDOWN_GRACE_SECONDS`, como 4 segundos frente a los 10 por defecto. Un `finally` no sirve
  aquí: la cancelación de nuke-di cae dentro de `cleanup()`, y los handlers en curso siguen adelante contra los
  clientes que se están desconectando.
- Los callbacks de `on_shutdown` se ejecutan al principio de `cleanup()`, antes de que espere a las peticiones en
  curso: ahí es donde se cierran las conexiones WebSocket o de server-sent events de larga duración. `on_startup`,
  `on_cleanup` y `cleanup_ctx` se ejecutan en `setup()` y `cleanup()`, mientras los clientes están conectados.

## <a id="websockets"></a>websockets

Comprobado con websockets 17.2, sobre su implementación asyncio, `websockets.asyncio.server`; `websockets.legacy`
está obsoleto desde la 14.0.

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

En una terminal:

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

En otra:

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

- Salir de `serve()` cierra todas las conexiones abiertas de una vez, con 1001 (going away). A diferencia de gRPC
  y aiohttp, un mensaje en curso pierde su respuesta: `classic -> 1` se contó y nunca se envió. El llamador de
  arriba falla ante el 1001; un cliente real debería reconectarse ante él, p. ej. con
  `async for websocket in connect(...)`, y enviar un mensaje que pueda enviarse de nuevo sin riesgo.
- `close_timeout`, 10 segundos por defecto, limita solo el handshake de cierre con cada cliente, y redúcelo de
  todos modos: el valor por defecto es todo el periodo de gracia por defecto. Nada limita a los handlers: salir de
  `serve()` espera a que retorne cada uno de ellos, así que un handler atascado retiene el worker más allá del
  periodo de gracia, y sigue adelante contra los clientes que se están desconectando una vez cancelado el worker.

## <a id="apscheduler"></a>APScheduler

Comprobado con APScheduler 3.11.3, la versión actual. Las tareas de APScheduler son métodos de un cliente:

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

Ctrl+C durante la segunda ejecución:

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

- `AsyncIOScheduler.shutdown()` no puede esperar a una tarea corrutina: solo programa la parada en el bucle de
  eventos, y la parada cancela las tareas que siguen en ejecución, diga lo que diga `wait=`. El worker pausa el
  planificador, para que no empiece ninguna ejecución nueva, y espera en `reports.between_runs()` a la ejecución
  en curso; solo entonces apaga el planificador. Sin esa espera, el mismo Ctrl+C termina la ejecución con
  `asyncio.exceptions.CancelledError` en `count_books`.
- El `finally` apaga el planificador también cuando el worker se cancela, p. ej. por una ejecución más larga que
  el periodo de gracia: la ejecución se cancela antes de que la base de datos se desconecte, en lugar de seguir
  adelante contra ella. `await asyncio.sleep(0)` deja que la parada programada ocurra antes de que el worker
  retorne.
- El planificador vive en el worker y no en un cliente propio: un cliente planificador arrancaría en cada
  contenedor que lo resuelva, incluido el de una app web, y el worker es dueño del orden de la parada, primero la
  ejecución en curso y después el planificador. Las tareas de APScheduler son métodos de un cliente, así que
  llegan a la base de datos a través de `self` y comparten el lock de `between_runs()`. Una función a nivel de
  módulo ligada con `DI.inject()` y pasada a `add_job()` también funciona, siempre que `inject()` se llame antes
  de que arranque el worker: dentro del worker el contenedor está conectado, e `inject()` falla.

### <a id="apscheduler-4"></a>APScheduler 4

APScheduler 4 es una alfa, comprobada con 4.0.0a6, y su API aún puede cambiar antes de la 4.0. `AsyncScheduler`
es un context manager asíncrono, y una planificación se añade con `await add_schedule()`. `Reports` se queda como
está; los imports y el worker pasan a ser:

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

APScheduler 4 ejecuta una planificación por intervalo de inmediato y luego cada 2 segundos. Su `stop()` también
cancela una tarea de APScheduler en ejecución, sin dejar rastro en la salida, así que la espera a la ejecución en
curso se queda.

## <a id="textual"></a>Textual

Comprobado con Textual 8.2.8. Una app de Textual no es un cliente: el worker la construye y le pasa los clientes
que recibió, igual que una prueba le pasa mocks.

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

La app ocupa la terminal, aquí de 60 columnas por 8 líneas:

```text
2 sci-fi books






 r Refresh  q Quit                              ▏^p palette
```

`q` la cierra, y el proceso termina con `0`:

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
0
```

SIGTERM, p. ej. `pkill -TERM -f app.tui` desde otra terminal, la cierra a través de `exit_on_shutdown`:

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
143
```

- `App.run()` arranca su propio bucle de eventos; dentro de un worker, `await app.run_async()` ejecuta la app en el
  bucle del worker. Cuando la app se cierra por sí sola, `exit_on_shutdown` sigue esperando: `BackgroundTasks` lo
  cancela al desconectarse, después de que el worker retorne.
- Textual desactiva las teclas de señal de la terminal: Ctrl+C es una tecla que pregunta "Do you want to quit?
  Press ctrl+q to quit the app", no SIGINT. Con `TEXTUAL_ALLOW_SIGNALS=1`, Ctrl+C envía SIGINT: la app se cierra
  y el proceso termina con `130`. SIGTERM llega a nuke-di en cualquier caso.
- Mientras la app se ejecuta, Textual captura `print()`; el worker imprime una vez que `run_async()` retorna. Los
  workers propios de Textual, `@work` y `run_worker()`, se ejecutan dentro de la app y no tienen nada que ver con
  `@worker`.

La app recibe sus clientes en `__init__`, así que una prueba le pasa un mock, con `run_test()` de Textual, bajo
pytest-asyncio con `asyncio_mode = auto` como en [Pruebas](testing.md):

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

Comprobado con temporalio 1.34.0 y el servidor de desarrollo de la Temporal CLI 1.9.1. Las activities hacen la
E/S, y la forma que tiene Temporal de darles una base de datos es una clase cuyos métodos son las activities,
construida una vez por worker de Temporal. Esa clase es un cliente:

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

La conexión con Temporal también es un cliente, uno que crea el `Client` de temporalio en `connect()`, como
cualquier [objeto de terceros](../../adr/0005-third-party-objects-as-client-classes.md):

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

El workflow vive en un módulo propio, que importan tanto el worker como el job que lo inicia:

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

El worker, detenido en medio de la segunda activity y arrancado de nuevo:

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

Los workflows, en otra terminal:

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

La activity en curso terminó dentro de `graceful_shutdown_timeout`, y Temporal guardó su resultado. El worker se
detuvo antes de que el workflow tomara ese resultado, así que `start` esperó; el siguiente proceso del worker
terminó el workflow sin volver a ejecutar la activity.

- `graceful_shutdown_timeout` es 0 por defecto: las activities en curso se cancelan en el momento en que el
  worker se apaga. Ponlo holgadamente por debajo de `SHUTDOWN_GRACE_SECONDS`: si nuke-di cancela el worker
  mientras la salida de `async with Worker(...)` sigue apagándose, las activities siguen adelante contra los
  clientes que se están desconectando.
- El `Client` de temporalio se llama igual que el de nuke-di: impórtalo como `TemporalClient`.
- El `Worker` de Temporal recibe las activities como métodos ligados de la instancia resuelta,
  `activities=[activities.count_books]`; un workflow las nombra por el método de la clase,
  `BookActivities.count_books`.

### <a id="workflows-never-take-clients"></a>Los workflows nunca reciben clientes

Un workflow es código que Temporal reproduce a partir de su historial, en cualquier worker y en cualquier
momento, dentro de un sandbox: debe ser determinista y no hacer E/S. Por eso un workflow nunca recibe un
cliente. `CountBooks` no tiene argumentos en `__init__`, Temporal lo construye y nuke-di nunca lo ve; todo lo que
accede a una base de datos, a una API HTTP o a una cola es una activity. El módulo del workflow importa
`BookActivities` solo para nombrar sus métodos, bajo `workflow.unsafe.imports_passed_through()`, de modo que el
sandbox usa el módulo ya importado en lugar de importarlo de nuevo.

Una prueba ejecuta el workflow en el servidor de pruebas de Temporal con salto de tiempo, con las activities
construidas a mano alrededor de un mock, bajo pytest-asyncio con `asyncio_mode = auto` como en
[Pruebas](testing.md):

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

La primera ejecución descarga el servidor de pruebas.
