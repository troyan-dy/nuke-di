# FastAPI

**English** · [Русский](../i18n/ru/fastapi.md) · [简体中文](../i18n/zh-CN/fastapi.md) · [Español](../i18n/es/fastapi.md) · [Português (Brasil)](../i18n/pt-BR/fastapi.md) · [日本語](../i18n/ja/fastapi.md) · [Polski](../i18n/pl/fastapi.md)

← [Documentation](../../README.md#documentation)

A FastAPI path operation takes a client the way a job does, by its type hint. Nothing else is
written per handler: no `Depends`, no `inject()`.

```bash
pip install "nuke-di[fastapi]"
```

Requires FastAPI 0.105 or newer. The examples share one module of clients:

```python
# app/clients.py
from nuke_di import Client


class Database(Client):
    async def connect(self) -> None:
        print("database: connected")

    async def disconnect(self) -> None:
        print("database: disconnected")

    async def fetch_user(self, user_id: int) -> str:
        return f"user-{user_id}"


class UserService(Client):
    def __init__(self, db: Database) -> None:
        self._db = db

    async def greet(self, user_id: int) -> str:
        return f"Hello, {await self._db.fetch_user(user_id)}!"
```

The API:

```python
# app/api.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di.fastapi import ClientRouter, setup

app = FastAPI()
setup(app)  # before the routes: clients connect on startup, disconnect on shutdown


@app.get("/users/{user_id}")
async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


account = ClientRouter(prefix="/me")


@account.get("")
async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user


app.include_router(account)
```

```console
$ uvicorn app.api:app
INFO:     Started server process [80948]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:54682 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:54684 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [80948]
```

```console
$ curl localhost:8000/users/42
"Hello, user-42!"
$ curl localhost:8000/me -H "X-User-Id: 7"
"user-7"
```

What happened:

1. `setup(app)` made every route declared on `app` afterwards fill its client arguments from the
   global `DI`, and wrapped the app's lifespan.
2. `@app.get` saw `users: UserService` and only recorded it; nothing was built on import.
3. On startup the lifespan resolved the clients of the routes the app serves, its own and those of
   the routers it includes, and connected them, each after its dependencies. On shutdown it
   disconnected them.
4. A request to `/users/42` got the connected `UserService`. `/me` went through the dependency
   `current_user`, which takes `db: Database` the same way.

The rules:

- **Where clients are filled.** In the arguments of path operations, websocket endpoints and every
  dependency they use, at any depth: functions, and classes used as `Depends(Auth)` or `Annotated[Auth, Depends()]`,
  including `dependencies=` of the route, of its router, of `include_router()` and of the app. An
  argument is a client when its type hint is a client, also inside `Annotated[UserService, ...]`
  without a `Depends`. Every other argument is FastAPI's: path, query, header, body, `Depends`.
- **Routers.** Create them with `ClientRouter(...)`, which takes the same arguments as `APIRouter`,
  and include them into the app or into another `ClientRouter`. `APIRouter(route_class=ClientRoute)`
  works for a router that includes no other routers. For another container, use
  `setup(app, container)` and `ClientRouter(container=container)`; including a router of another
  container raises `TypeError` at once.
- **Call `setup(app)` before the routes.** A route with a client declared before it fails at once
  with the `TypeError` described [below](#not-supported).
- **Only what the app serves.** A router that the app does not include, e.g. one imported only by a
  test, connects nothing on the app's startup.
- **Instances.** As with `inject()`, a `Client` is one instance per container, and a
  `NotSingletonClient` is one instance per argument that declares it, not one per request.
- **Lifespan.** The app's own `lifespan=` runs inside: its startup code sees connected clients, and
  its shutdown code runs before they disconnect. On shutdown `Shutdown` is set and
  `BackgroundTasks` are stopped, if the app uses them, before the clients disconnect, as in a
  worker. FastAPI's own `BackgroundTasks` is a different class and is not a client.
- **The function stays a function.** Its signature now shows `Annotated[UserService, Depends(...)]`
  to FastAPI, but calling it directly with a client, e.g. in a unit test, works as before.

**Testing.** Importing the app builds nothing, so a test replaces a client before `TestClient`
starts the app, with [`override()`](testing.md) or the `global_di` fixture:

```python
# tests/test_api.py
from fastapi.testclient import TestClient

from app.api import app
from app.clients import Database
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_get_user() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/users/1").json() == "Hello, alice!"
        assert client.get("/me", headers={"X-User-Id": "7"}).json() == "alice"
```

```console
$ pytest -q tests/test_api.py
.                                                                        [100%]
1 passed in 0.16s
```

`app.dependency_overrides` keeps working, also for a dependency function that takes clients.

**Websockets.** A websocket endpoint takes clients the same way, on the app or on a `ClientRouter`:

```python
# app/chat.py
from fastapi import FastAPI, WebSocket

from app.clients import UserService
from nuke_di.fastapi import setup

app = FastAPI()
setup(app)


@app.websocket("/greet")
async def greet(websocket: WebSocket, users: UserService) -> None:
    await websocket.accept()
    async for user_id in websocket.iter_text():
        await websocket.send_text(await users.greet(int(user_id)))
```

```python
# tests/test_chat.py
from fastapi.testclient import TestClient

from app.chat import app


def test_greet() -> None:
    with TestClient(app) as client, client.websocket_connect("/greet") as ws:
        ws.send_text("42")
        assert ws.receive_text() == "Hello, user-42!"
```

```console
$ pytest -q tests/test_chat.py
.                                                                        [100%]
1 passed in 0.16s
```

**A client that fails to connect** fails the startup. The lifespan raises a plain `RuntimeError`
from the `ConnectError`, since a `SystemExit` would escape the server's event loop, and the server
reports it and exits:

```python
# app/broken.py
from fastapi import FastAPI

from nuke_di import Client
from nuke_di.fastapi import setup


class Kafka(Client):
    async def connect(self) -> None:
        raise OSError("broker kafka-1:9092 is unreachable")


app = FastAPI()
setup(app)


@app.post("/events")
async def publish(kafka: Kafka) -> None: ...
```

```console
$ uvicorn app.broken:app
INFO:     Started server process [81379]
INFO:     Waiting for application startup.
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
ERROR:    Traceback (most recent call last):
  ...
nuke_di.errors.ConnectError: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ...
RuntimeError: nuke-di clients failed to start: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable

ERROR:    Application startup failed. Exiting.
$ echo $?
3
```

## Not supported

These places take no clients. Each raises a `TypeError` that says so when the route is declared:

| Place                                                   | Instead                                       |
|---------------------------------------------------------|-----------------------------------------------|
| A router created without `ClientRouter` / `ClientRoute` | Create it with `ClientRouter(...)`            |
| A websocket endpoint on `APIRouter(route_class=ClientRoute)` | Create the router with `ClientRouter(...)` |
| An optional client, `Database \| None`                  | A plain `Database`                            |
| A bound method or a callable object as an endpoint or a dependency | A function or a class              |

Unlike these, a route of a router included into a plain `APIRouter` instead of a `ClientRouter` is
found by older FastAPI only. On FastAPI 0.14x it is declared and the app starts, but its requests fail
with `RuntimeError: UserService was not started with the app: include the router of its route into
the app or into a ClientRouter, not into a plain APIRouter`.

A request that arrives without the lifespan, e.g. through `TestClient(app)` without `with`, gets a
`RuntimeError`: `UserService is not connected: start the app with its lifespan`.
