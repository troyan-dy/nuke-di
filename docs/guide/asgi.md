# Starlette, Quart and any ASGI app

**English** · [Русский](../i18n/ru/asgi.md) · [简体中文](../i18n/zh-CN/asgi.md) · [Español](../i18n/es/asgi.md) · [Português (Brasil)](../i18n/pt-BR/asgi.md) · [日本語](../i18n/ja/asgi.md) · [Polski](../i18n/pl/asgi.md)

← [Documentation](../../README.md#documentation)

A framework without dependency injection, such as Starlette or Quart, cannot fill a handler's arguments by
type hint. `nuke_di.asgi.lifespan()` covers the rest: it is the app's lifespan, which connects the clients
it lists when the app starts and disconnects them when it stops, and a handler asks it for a client with
`get()`. It imports no framework and needs no extra:

```bash
pip install nuke-di
```

- [Starlette](#starlette)
- [The app's own lifespan](#the-apps-own-lifespan)
- [Testing](#testing)
- [Quart](#quart)
- [aiohttp](#aiohttp)
- [A plain ASGI app](#a-plain-asgi-app)
- [Errors](#errors)

## <a id="starlette"></a>Starlette

With the clients of the [FastAPI](fastapi.md) examples:

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

The rules:

- **The list is explicit.** There is no route table to find the clients in, so `lifespan(container,
  *clients)` names every client a handler takes. `clients.get(Database)` works only when `Database` is
  listed, even though it connects anyway as a dependency of `UserService`: a handler that relied on that
  would break the day `UserService` stops depending on it.
- **`get()` is typed.** `clients.get(UserService)` returns a `UserService` for mypy and pyright, unlike
  an attribute of `request.state`. It is a plain method, so it works in a handler, a websocket endpoint, a
  middleware or a background task, and the module that holds `clients` is imported by the handlers like
  any other.
- **Resolved on startup.** Creating `clients` resolves nothing; every startup resolves the listed clients
  again, so `override()` before the app starts replaces one, and a second start gets fresh clients.
- **Shutdown.** On shutdown `Shutdown` is set, the `BackgroundTasks` are stopped and then the clients
  disconnect, the same order as in a [worker](workers-and-jobs.md).
- **One app at a time.** A container connects once: a second app started on the same container while
  the first runs fails its startup.
- **FastAPI too.** A FastAPI app that keeps the signatures of its routes as written passes
  `FastAPI(lifespan=clients)` the same way; with `nuke_di.fastapi.setup()` its routes take clients by
  type hint instead, see [FastAPI](fastapi.md).

## <a id="the-apps-own-lifespan"></a>The app's own lifespan

`clients(app)` is an async context manager, so the app's own lifespan enters it first and runs its startup
and shutdown code inside, with the clients connected. What it yields is the app's state, as usual:

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

## <a id="testing"></a>Testing

A test replaces a client before `TestClient` starts the app:

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

`TestClient(app)` without `with` sends requests without running the lifespan, and a handler says so:

```console
$ python -c "from starlette.testclient import TestClient; from app.starlette_api import app; TestClient(app).get('/users/1')"
Traceback (most recent call last):
  ...
RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)`
```

## <a id="quart"></a>Quart

Quart has no `lifespan=` argument: it runs `before_serving` hooks on startup and `after_serving` hooks on
shutdown. One `AsyncExitStack` keeps the clients connected in between:

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

A test starts the app with `test_app()`:

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

Quart calls the hooks of each kind in the order they are registered: `connect` goes before your own
`before_serving` hooks, so they see the clients, and `disconnect` after your own `after_serving` hooks.
Quart's `while_serving` would read shorter, but it creates its generator once, when it is registered, so the
app could start only once per process, and the second test that starts it fails.

## <a id="aiohttp"></a>aiohttp

aiohttp 3.14 takes an async context manager in `cleanup_ctx`, and `clients` is one:

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

An older aiohttp takes an async generator instead:

```python
from collections.abc import AsyncIterator


async def run_clients(app: web.Application) -> AsyncIterator[None]:
    async with clients(app):
        yield


app.cleanup_ctx.append(run_clients)
```

## <a id="a-plain-asgi-app"></a>A plain ASGI app

An ASGI app without a framework answers the lifespan messages of the server itself. `clients()` takes no
argument there:

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

## <a id="errors"></a>Errors

| When | What is raised |
|---|---|
| A handler runs without the app's lifespan, or after it stopped | ``RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` `` |
| `get()` of a client the list lacks | ``RuntimeError: Database is not a client of this lifespan: list it in `lifespan(container, ...)` `` |
| A client's `connect()` fails | `RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused`; the server reports a failed startup and exits, and the container is left flushed |
| The container is connected already, e.g. by another app | `RuntimeError: nuke-di clients failed to start: the container is already connected` |
| `lifespan(Database)`, the container left out | `TypeError: lifespan() takes the container first, then the clients, e.g. lifespan(DI, Database); got <class 'app.clients.Database'>` |
| `lifespan(DI, "Database")`, not a client class | `TypeError: 'Database' is not a client: subclass Client or NotSingletonClient` |

Under uvicorn, with a `Database` whose `connect()` raises `OSError("connection refused")`:

```console
$ uvicorn app.broken_api:app
INFO:     Started server process [44319]
INFO:     Waiting for application startup.
Database.connect() raised OSError: connection refused
...
RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused

ERROR:    Application startup failed. Exiting.
```
