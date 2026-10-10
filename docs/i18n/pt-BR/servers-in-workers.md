# <a id="servers-inside-a-worker"></a>Servidores dentro de um worker

[English](../../guide/servers-in-workers.md) · [Русский](../ru/servers-in-workers.md) · [简体中文](../zh-CN/servers-in-workers.md) · [Español](../es/servers-in-workers.md) · **Português (Brasil)** · [日本語](../ja/servers-in-workers.md) · [Polski](../pl/servers-in-workers.md)

← [Documentação](../README.pt-BR.md#documentation)

grpc.aio, aiohttp, websockets, APScheduler, Textual e Temporal não têm injeção de dependências própria, então o
nuke-di não tem um módulo para eles, e nem precisa: o servidor roda dentro de um [`@worker`](workers-and-jobs.md#your-first-worker),
e a classe cujos métodos são os handlers dele é um `Client`.

## <a id="the-pattern"></a>O padrão

- **Os handlers são métodos de um cliente.** Um servicer gRPC, uma classe de views do aiohttp, um handler de
  WebSocket, uma classe de jobs agendados, as activities do Temporal: cada um recebe seus clientes no `__init__`, e o
  worker o recebe pelo type hint. Ele é resolvido, e seus clientes são conectados, antes de o servidor iniciar; um
  handler chega até eles por meio de `self`.
- **O worker é dono do servidor.** Ele constrói e inicia o servidor, espera o
  [`Shutdown`](workers-and-jobs.md#your-first-worker), para o servidor e retorna; então os clientes se desconectam.
  O código de saída, os logs e os hooks são os de [qualquer worker](workers-and-jobs.md#exit-codes).
- **O servidor para dentro do período de tolerância.** Ao parar, ele deixa terminar as chamadas em andamento, e cada
  servidor tem seu próprio timeout para isso. Mantenha-o abaixo de `SHUTDOWN_GRACE_SECONDS`: um worker que ainda está
  parando depois do [período de tolerância](workers-and-jobs.md#grace-period) é cancelado, e as chamadas em andamento
  junto com ele.

| Servidor | O cliente | Parado por | Seu timeout, por padrão |
|---|---|---|---|
| [grpc.aio](#grpcaio) | O servicer | `await server.stop(grace)` | `grace`, obrigatório; `None` aborta as chamadas em andamento |
| [aiohttp](#aiohttp) | Uma classe de views | `await runner.cleanup()` | `shutdown_timeout`, 60 s |
| [websockets](#websockets) | Uma classe com o handler da conexão | Sair do `async with serve(...)` | `close_timeout`, 10 s |
| [APScheduler](#apscheduler) | Uma classe de jobs | `scheduler.shutdown()` | Sem timeout: um job em execução é cancelado |
| [Textual](#textual) | Nenhum: o `App` recebe seus clientes do worker | `app.exit()` | Sem timeout |
| [Temporal](#temporal) | Uma classe de activities | Sair do `async with Worker(...)` | `graceful_shutdown_timeout`, 0 |

As receitas compartilham um mesmo módulo de clientes: um banco de dados cuja consulta leva um segundo, para que
uma chamada ainda esteja em andamento quando o processo for parado.

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

Cada receita envia SIGTERM no meio de uma chamada, do jeito que o Kubernetes para um pod.

## <a id="grpcaio"></a>grpc.aio

Verificado com grpcio 1.84.0.

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

O servicer é o cliente:

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

Um job para chamá-lo:

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

O SIGTERM chegou no meio da segunda chamada: a chamada terminou e recebeu sua resposta, depois o servidor parou,
e depois o banco de dados se desconectou.

- `server.stop(grace)` recusa novas chamadas imediatamente, dá `grace` segundos às chamadas em andamento e depois
  as aborta. Mantenha `grace` abaixo de `SHUTDOWN_GRACE_SECONDS`, senão o nuke-di cancela o worker antes.
- Rode o `protoc` a partir da raiz do projeto com `-I.`: assim o `books_pb2_grpc.py` gerado importa
  `from app import books_pb2`, e o `--pyi_out` fornece as classes de mensagem a um type checker.

## <a id="aiohttp"></a>aiohttp

Verificado com aiohttp 3.14.4. Os handlers são métodos de um cliente, adicionados às rotas como métodos vinculados:

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

Em um terminal:

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

Em outro:

```console
$ curl -s -w '\n' localhost:8080/books/sci-fi/count
{"genre": "sci-fi", "count": 2}
$ curl -s -w '\n' localhost:8080/books/classic/count & sleep 0.5; pkill -TERM -f app.aiohttp_server; wait
{"genre": "classic", "count": 1}
```

- `web.run_app()` não pode rodar dentro de um worker: ele inicia um event loop próprio e trata o SIGINT e o
  SIGTERM por conta própria. `AppRunner` e `TCPSite` rodam a mesma aplicação no loop do worker e deixam os sinais
  para o nuke-di.
- `shutdown_timeout`, quanto tempo o `cleanup()` espera pelas requisições em andamento, é de 60 segundos por padrão:
  defina-o abaixo de `SHUTDOWN_GRACE_SECONDS`.
- Os `on_startup`, `on_cleanup` e `cleanup_ctx` da própria aplicação continuam rodando, em `setup()` e
  `cleanup()`, enquanto os clientes estão conectados.

## <a id="websockets"></a>websockets

Verificado com websockets 17.2, na sua implementação asyncio, `websockets.asyncio.server`; `websockets.legacy`
está obsoleto desde a 14.0.

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

Em um terminal:

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

Em outro:

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

- Sair do `serve()` fecha todas as conexões abertas de uma vez, com 1001 (going away). Diferente do gRPC e do
  aiohttp, uma mensagem em andamento perde sua resposta: `classic -> 1` foi contado e nunca enviado. De todo modo,
  um cliente WebSocket se reconecta ao receber 1001, então faça com que uma mensagem possa ser reenviada com segurança.
- `close_timeout`, quanto tempo um cliente pode levar para responder ao handshake de fechamento, é de 10 segundos
  por padrão, tanto quanto o período de tolerância padrão inteiro: reduza-o.

## <a id="apscheduler"></a>APScheduler

Verificado com APScheduler 3.11.3, a versão atual. Os jobs são métodos de um cliente:

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

Ctrl+C durante a segunda execução:

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

- `AsyncIOScheduler.shutdown()` não consegue esperar por um job de corrotina: ele cancela os jobs ainda em execução,
  seja qual for o valor de `wait=`. O lock deixa a execução em andamento terminar: o worker pausa o scheduler, pega
  o lock assim que a execução o libera, e só então desliga o scheduler. Sem o lock, o mesmo Ctrl+C encerra a execução
  com `asyncio.exceptions.CancelledError` em `count_books`.
- `start()` usa o event loop em execução, então o scheduler é criado e iniciado dentro do worker, e não na
  importação. Um job é um método de um cliente, e não uma função vinculada com `DI.inject()`: dentro de um worker o
  container já está conectado, e `inject()` só funciona antes de ele se conectar.

### <a id="apscheduler-4"></a>APScheduler 4

O APScheduler 4 é uma pré-release, verificada com 4.0.0a6, e sua API é nova: `AsyncScheduler` é um gerenciador de
contexto assíncrono, e um agendamento é adicionado com `await add_schedule()`. `Reports` continua como está; os
imports e o worker ficam assim:

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

O APScheduler 4 executa um agendamento por intervalo imediatamente, e depois a cada 2 segundos. O `stop()` dele
também cancela um job em execução, sem dizer nada na saída, então o lock continua.

## <a id="textual"></a>Textual

Verificado com Textual 8.2.8. Uma aplicação Textual não é um cliente: o worker a constrói e lhe passa os clientes
que recebeu, como um teste passa fakes.

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

A aplicação ocupa o terminal, aqui com 60 colunas por 8 linhas:

```text
2 sci-fi books






 r Refresh  q Quit                              ▏^p palette
```

`q` a fecha, e o processo termina com `0`; o SIGTERM a fecha por meio de `exit_on_shutdown`, com `143`:

```console
$ python -m app.tui
database: connected
tui: closed
database: disconnected
$ echo $?
0
```

- `App.run()` inicia um event loop próprio; dentro de um worker, `await app.run_async()` roda a aplicação no loop
  do worker. `BackgroundTasks` cancela `exit_on_shutdown` quando a aplicação se fecha sozinha.
- O Textual desativa as teclas de sinal do terminal: Ctrl+C é uma tecla que pergunta "Do you want to quit? Press
  ctrl+q to quit the app", e não SIGINT. Com `TEXTUAL_ALLOW_SIGNALS=1`, o Ctrl+C envia SIGINT: a aplicação se fecha e
  o processo termina com `130`. O SIGTERM chega ao nuke-di de qualquer forma.
- Enquanto a aplicação roda, o Textual captura o `print()`; o worker imprime assim que `run_async()` retorna. Os
  workers do próprio Textual, `@work` e `run_worker()`, rodam dentro da aplicação e não têm nada a ver com `@worker`.

A aplicação recebe seus clientes no `__init__`, então um teste passa um mock, com o `run_test()` do Textual:

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

Verificado com temporalio 1.34.0 e o servidor de desenvolvimento da Temporal CLI 1.9.1. As activities fazem o I/O,
e o jeito do Temporal de lhes dar um banco de dados é uma classe cujos métodos são as activities, construída uma vez
por worker. Essa classe é um cliente:

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

A conexão com o Temporal também é um cliente, um que cria o `Client` do temporalio em `connect()`, como qualquer
[objeto de terceiros](../../adr/0005-third-party-objects-as-client-classes.md):

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

O workflow fica em um módulo próprio, que tanto o worker quanto o job que o inicia importam:

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

O worker, parado no meio da segunda activity e iniciado de novo:

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

Os workflows, em outro terminal:

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

A activity em andamento terminou dentro do `graceful_shutdown_timeout`, e o Temporal guardou o resultado dela. O
worker parou antes de o workflow pegar esse resultado, então o `start` ficou esperando; o worker seguinte terminou
o workflow sem executar a activity de novo.

- `graceful_shutdown_timeout` é 0 por padrão: as activities em andamento são canceladas no instante em que o worker
  desliga. Defina-o abaixo de `SHUTDOWN_GRACE_SECONDS`.
- O `Client` do temporalio tem o mesmo nome que o do nuke-di: importe-o como `TemporalClient`.
- O worker recebe as activities como métodos vinculados da instância resolvida, `activities=[activities.count_books]`;
  um workflow as nomeia pelo método da classe, `BookActivities.count_books`.

### <a id="workflows-never-take-clients"></a>Workflows nunca recebem clientes

Um workflow é um código que o Temporal reexecuta a partir do seu histórico, em qualquer worker e a qualquer momento,
dentro de um sandbox: ele precisa ser determinístico e não fazer I/O. Por isso um workflow nunca recebe um cliente.
`CountBooks` não tem argumentos no `__init__`, o Temporal o constrói, e o nuke-di nunca o vê; tudo o que acessa um
banco de dados, uma API HTTP ou uma fila é uma activity. O módulo do workflow importa `BookActivities` só para nomear
os métodos dela, dentro de `workflow.unsafe.imports_passed_through()`, para que o sandbox use o módulo já importado
em vez de importá-lo de novo.

Um teste roda o workflow no servidor de testes com avanço de tempo do Temporal, com as activities construídas à mão
em torno de um mock:

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

A primeira execução baixa o servidor de testes.
