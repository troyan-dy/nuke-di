# <a id="starlette-quart-and-any-asgi-app"></a>Starlette, Quart i dowolna aplikacja ASGI

[English](../../guide/asgi.md) · [Русский](../ru/asgi.md) · [简体中文](../zh-CN/asgi.md) · [Español](../es/asgi.md) · [Português (Brasil)](../pt-BR/asgi.md) · [日本語](../ja/asgi.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

Framework bez wstrzykiwania zależności, taki jak Starlette czy Quart, nie potrafi wypełnić argumentów
handlera po adnotacji typu. Resztę załatwia `nuke_di.asgi.lifespan()`: to lifespan aplikacji, który łączy
wymienionych w nim klientów, gdy aplikacja startuje, i rozłącza ich, gdy się zatrzymuje, a handler pobiera
od niego klienta przez `get()`. Moduł nie importuje żadnego frameworka i nie wymaga dodatku (extra):

```bash
pip install nuke-di
```

- [Starlette](#starlette)
- [Własny lifespan aplikacji](#the-apps-own-lifespan)
- [Testowanie](#testing)
- [Quart](#quart)
- [aiohttp](#aiohttp)
- [Aplikacja ASGI bez frameworka](#a-plain-asgi-app)
- [Błędy](#errors)

## <a id="starlette"></a>Starlette

Z klientami z przykładów dla [FastAPI](fastapi.md):

```python
# app/starlette_api.py
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.clients import Database, UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

# The clients the handlers take: they connect, with their dependencies, when the app starts
clients = lifespan(DI, UserService, Database)


async def get_user(request: Request) -> PlainTextResponse:
    users = clients.get(UserService)
    return PlainTextResponse(await users.greet(request.path_params["user_id"]))


async def me(request: Request) -> PlainTextResponse:
    db = clients.get(Database)
    return PlainTextResponse(await db.fetch_user(int(request.headers["X-User-Id"])))


app = Starlette(
    routes=[Route("/users/{user_id:int}", get_user), Route("/me", me)],
    lifespan=clients,
)
```

```console
$ uvicorn app.starlette_api:app
INFO:     Started server process [47010]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:50476 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:50478 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [47010]
```

```console
$ curl localhost:8000/users/42
Hello, user-42!
$ curl localhost:8000/me -H "X-User-Id: 7"
user-7
```

Zasady:

- **Lista jest jawna.** Nie ma tablicy tras, w której można by znaleźć klientów, więc
  `lifespan(container, *clients)` wymienia każdego klienta, którego przyjmuje handler.
  `clients.get(Database)` działa tylko wtedy, gdy `Database` jest na liście, choć i tak łączy się jako
  zależność `UserService`: handler, który by na tym polegał, zepsułby się w dniu, w którym `UserService`
  przestanie od niego zależeć.
- **`get()` jest typowany.** `clients.get(UserService)` zwraca `UserService` dla mypy i pyright,
  w przeciwieństwie do atrybutu `request.state`. To zwykła metoda, więc działa w handlerze, endpoincie
  websocket, middleware czy zadaniu w tle, a moduł, w którym jest `clients`, handlery importują jak każdy
  inny.
- **Rozwiązywani przy starcie.** Utworzenie `clients` niczego nie rozwiązuje; każdy start rozwiązuje
  wymienionych klientów od nowa, więc `override()` przed startem aplikacji podmienia klienta, a drugi start
  dostaje nowych klientów.
- **Zamykanie.** Przy zamykaniu ustawiany jest `Shutdown`, zatrzymywane są `BackgroundTasks`, a dopiero
  potem klienci się rozłączają — w tej samej kolejności co w [workerze](workers-and-jobs.md).
- **Jedna aplikacja naraz.** Kontener łączy się raz: druga aplikacja uruchomiona na tym samym kontenerze,
  gdy pierwsza wciąż działa, nie przechodzi startu.
- **FastAPI też.** Aplikacja FastAPI, która zostawia sygnatury swoich tras bez zmian, przekazuje
  `FastAPI(lifespan=clients)` w ten sam sposób; z `nuke_di.fastapi.setup()` jej trasy przyjmują za to
  klientów po adnotacji typu, zob. [FastAPI](fastapi.md).

## <a id="the-apps-own-lifespan"></a>Własny lifespan aplikacji

`clients(app)` jest asynchronicznym menedżerem kontekstu, więc własny lifespan aplikacji najpierw do niego
wchodzi, a swój kod startowy i zamykający wykonuje wewnątrz, przy połączonych klientach. To, co zwraca
przez `yield`, jak zwykle jest stanem aplikacji:

```python
# app/own_lifespan.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.clients import UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService)


@asynccontextmanager
async def app_lifespan(app: Starlette) -> AsyncIterator[dict[str, str]]:
    async with clients(app):
        # The app's own startup and shutdown code runs with the clients connected
        greeting = await clients.get(UserService).greet(0)
        print("warmed up:", greeting)
        yield {"greeting": greeting}
        print("app: stopping")


async def index(request: Request) -> PlainTextResponse:
    return PlainTextResponse(request.state.greeting)


app = Starlette(routes=[Route("/", index)], lifespan=app_lifespan)
```

```console
$ uvicorn app.own_lifespan:app
INFO:     Started server process [46993]
INFO:     Waiting for application startup.
database: connected
warmed up: Hello, user-0!
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:50465 - "GET / HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
app: stopping
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [46993]
```

## <a id="testing"></a>Testowanie

Test podmienia klienta, zanim `TestClient` wystartuje aplikację:

```python
# tests/test_starlette_api.py
from starlette.testclient import TestClient

from app.clients import Database
from app.starlette_api import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_get_user() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/users/1").text == "Hello, alice!"
        assert client.get("/me", headers={"X-User-Id": "7"}).text == "alice"
```

```console
$ pytest -q tests/test_starlette_api.py
.                                                                        [100%]
1 passed in 0.04s
```

`TestClient(app)` bez `with` wysyła żądania bez uruchamiania lifespan, a handler to zgłasza:

```console
$ python -c "from starlette.testclient import TestClient; from app.starlette_api import app; TestClient(app).get('/users/1')"
Traceback (most recent call last):
  ...
RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)`
```

## <a id="quart"></a>Quart

Quart nie ma argumentu `lifespan=`: przy starcie wykonuje hooki `before_serving`, a przy zamykaniu hooki
`after_serving`. Jeden `AsyncExitStack` utrzymuje połączenie klientów pomiędzy nimi:

```python
# app/quart_api.py
from contextlib import AsyncExitStack

from quart import Quart, request

from app.clients import Database, UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService, Database)
app = Quart(__name__)
running = AsyncExitStack()


@app.before_serving
async def connect() -> None:
    await running.enter_async_context(clients(app))


@app.after_serving
async def disconnect() -> None:
    await running.aclose()


@app.get("/users/<int:user_id>")
async def get_user(user_id: int) -> str:
    return await clients.get(UserService).greet(user_id)


@app.get("/me")
async def me() -> str:
    return await clients.get(Database).fetch_user(int(request.headers["X-User-Id"]))
```

```console
$ uvicorn app.quart_api:app
INFO:     Started server process [44067]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:49372 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:49374 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [44067]
```

Test uruchamia aplikację przez `test_app()`:

```python
# tests/test_quart_api.py
from app.clients import Database
from app.quart_api import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_get_user() -> None:
    with DI.override(Database, FakeDatabase()):
        async with app.test_app() as test_app:
            response = await test_app.test_client().get("/users/1")
            assert await response.get_data(as_text=True) == "Hello, alice!"
```

```console
$ pytest -q tests/test_quart_api.py
.                                                                        [100%]
1 passed in 0.15s
```

Quart wywołuje hooki danego rodzaju w kolejności rejestracji: `connect` wykonuje się przed twoimi własnymi
hookami `before_serving`, więc one widzą już klientów, a `disconnect` po twoich hookach `after_serving`.
Z `while_serving` z Quart kod byłby krótszy, ale Quart tworzy jego generator raz, przy rejestracji, więc
aplikacja mogłaby wystartować tylko raz na proces, a drugi test, który ją uruchamia, by nie przeszedł.

## <a id="aiohttp"></a>aiohttp

aiohttp 3.14 przyjmuje w `cleanup_ctx` asynchroniczny menedżer kontekstu, a `clients` właśnie nim jest:

```python
# app/aiohttp_api.py
from aiohttp import web

from app.clients import Database, UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService, Database)


async def get_user(request: web.Request) -> web.Response:
    users = clients.get(UserService)
    return web.Response(text=await users.greet(int(request.match_info["user_id"])))


app = web.Application()
app.router.add_get("/users/{user_id}", get_user)
app.cleanup_ctx.append(clients)  # aiohttp 3.14 or newer

if __name__ == "__main__":
    web.run_app(app)
```

```console
$ python -m app.aiohttp_api
database: connected
======== Running on http://0.0.0.0:8080 ========
(Press CTRL+C to quit)
^C
database: disconnected
```

```console
$ curl localhost:8080/users/42
Hello, user-42!
```

Starszy aiohttp przyjmuje zamiast tego asynchroniczny generator:

```python
from collections.abc import AsyncIterator


async def run_clients(app: web.Application) -> AsyncIterator[None]:
    async with clients(app):
        yield


app.cleanup_ctx.append(run_clients)
```

## <a id="a-plain-asgi-app"></a>Aplikacja ASGI bez frameworka

Aplikacja ASGI bez frameworka sama odpowiada na komunikaty lifespan serwera. `clients()` nie przyjmuje
tam argumentu:

```python
# app/raw_asgi.py
from typing import Any

from app.clients import UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService)


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        await receive()  # lifespan.startup
        started = False
        try:
            async with clients():
                await send({"type": "lifespan.startup.complete"})
                started = True
                await receive()  # lifespan.shutdown
        except Exception as exc:
            await send({"type": f"lifespan.{'shutdown' if started else 'startup'}.failed", "message": str(exc)})
            raise
        await send({"type": "lifespan.shutdown.complete"})
        return

    body = (await clients.get(UserService).greet(42)).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})
```

```console
$ uvicorn app.raw_asgi:app
INFO:     Started server process [48646]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:50998 - "GET / HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [48646]
```

## <a id="errors"></a>Błędy

| Kiedy | Co jest zgłaszane |
|---|---|
| Handler działa bez lifespan aplikacji albo po jego zatrzymaniu | ``RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` `` |
| `get()` klienta, którego brakuje na liście | ``RuntimeError: Database is not a client of this lifespan: list it in `lifespan(container, ...)` `` |
| `connect()` klienta kończy się błędem | `RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused`; serwer zgłasza nieudany start i kończy działanie, a kontener zostaje wyczyszczony, jak po `flush()` |
| Kontener jest już połączony, np. przez inną aplikację | `RuntimeError: nuke-di clients failed to start: the container is already connected` |
| `lifespan(Database)`, bez kontenera | `TypeError: lifespan() takes the container first, then the clients, e.g. lifespan(DI, Database); got <class 'app.clients.Database'>` |
| `lifespan(DI, "Database")`, nie klasa klienta | `TypeError: 'Database' is not a client: subclass Client or NotSingletonClient` |

Pod uvicorn, z `Database`, którego `connect()` zgłasza `OSError("connection refused")`:

```console
$ uvicorn app.broken_api:app
INFO:     Started server process [44319]
INFO:     Waiting for application startup.
Database.connect() raised OSError: connection refused
...
RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused

ERROR:    Application startup failed. Exiting.
```
