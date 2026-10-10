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
- **One container per app.** Each app gets the clients of the container given to its `setup()`, so two
  apps on two containers serve the same functions at once, e.g. in tests. An app mounted into a set-up app,
  or one that serves its routes, e.g. through `include_router(api.router)`, which runs `api`'s lifespan,
  gets the clients that app started.
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

## Class-based views

Routes that share what they need, the user of the request and a few clients, take it as one class. The
class is a FastAPI dependency with a type-hinted `__init__`, written the way a client is, and its
`__init__` takes request data and clients side by side:

```python
# app/views.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di.fastapi import setup

app = FastAPI()
setup(app)


class Account:
    # Built by FastAPI for every request, from a header and two clients
    def __init__(self, x_user_id: Annotated[int, Header()], db: Database, users: UserService) -> None:
        self.user_id = x_user_id
        self.db = db
        self.users = users

    async def name(self) -> str:
        return await self.db.fetch_user(self.user_id)

    async def greeting(self) -> str:
        return await self.users.greet(self.user_id)


CurrentAccount = Annotated[Account, Depends()]


@app.get("/me")
async def me(account: CurrentAccount) -> str:
    return await account.name()


@app.get("/me/greeting")
async def greeting(account: CurrentAccount) -> str:
    return await account.greeting()
```

```console
$ uvicorn app.views:app
INFO:     Started server process [50908]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51798 - "GET /me HTTP/1.1" 200 OK
INFO:     127.0.0.1:51800 - "GET /me/greeting HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [50908]
```

```console
$ curl localhost:8000/me -H "X-User-Id: 7"
"user-7"
$ curl localhost:8000/me/greeting -H "X-User-Id: 7"
"Hello, user-7!"
```

A test replaces a client once for every route that uses the class:

```python
# tests/test_views.py
from fastapi.testclient import TestClient

from app.clients import Database
from app.views import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_account() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/me", headers={"X-User-Id": "7"}).json() == "alice"
        assert client.get("/me/greeting", headers={"X-User-Id": "7"}).json() == "Hello, alice!"
```

```console
$ pytest -q tests/test_views.py
.                                                                        [100%]
1 passed in 0.18s
```

The rules:

- **One view per request, one client per container.** FastAPI builds an `Account` for every request;
  the `db` and `users` in it are the connected clients of the container, the same objects in every
  request. nuke-di rewrote the signature of the class, as it does for a dependency function, and left
  its `__init__` alone: `Account(x_user_id=7, db=db, users=users)` in a unit test works as before. A
  dataclass works the same way, its fields being the arguments.
- **A view that needs nothing from the request is a client.** `class Account(Client)` taken as
  `account: Account` is built once per container and connects with the others. A client class written
  as `Annotated[Account, Depends()]`, e.g. one decorated with `@client_dataclass`, is built by FastAPI
  for every request instead, and its `connect()` never runs: leave the `Depends()` out.
- **`@cbv` of fastapi-utils is not needed.** It declares the routes again on a plain `APIRouter` of its
  own, so a class attribute typed as a client fails at `include_router()` with
  `TypeError: Database is a nuke-di client, not a pydantic type`. The class above shares the clients
  between routes with nothing but FastAPI.

## An app per test container

An app factory builds the app around the container it is given, so every test runs on a container of
its own and the server on the global `DI`. The functions stay at module level:

```python
# app/factory.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di import DI, Dependencies
from nuke_di.fastapi import ClientRouter, setup


async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user


def make_app(container: Dependencies) -> FastAPI:
    app = FastAPI()
    setup(app, container)
    app.add_api_route("/users/{user_id}", get_user)

    # A router fills clients from one container, so every app creates its own
    account = ClientRouter(prefix="/me", container=container)
    account.add_api_route("", me)
    app.include_router(account)
    return app


def create_app() -> FastAPI:
    # For the server: `uvicorn --factory app.factory:create_app`
    return make_app(DI)
```

```console
$ uvicorn --factory app.factory:create_app
INFO:     Started server process [50968]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51815 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51817 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [50968]
```

The tests build an app on the `di` fixture:

```python
# tests/test_factory.py
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.clients import Database
from app.factory import current_user, make_app
from nuke_di import Dependencies


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


@pytest.fixture
def app(di: Dependencies) -> Iterator[FastAPI]:
    # `di` is a fresh container for every test, a fixture of nuke-di
    with di.override(Database, FakeDatabase()):
        yield make_app(di)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_get_user(client: TestClient) -> None:
    assert client.get("/users/1").json() == "Hello, alice!"


def test_me(client: TestClient) -> None:
    assert client.get("/me", headers={"X-User-Id": "7"}).json() == "alice"


def test_dependency_overrides(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[current_user] = lambda: "carol"
    assert client.get("/me").json() == "carol"
```

```console
$ pytest -q tests/test_factory.py
...                                                                      [100%]
3 passed in 0.16s
```

The rules:

- **One `override()` for the whole app.** `di.override(Database, FakeDatabase())` replaces the database
  for the dependency `current_user`, for `UserService` and for everything else that takes a `Database`,
  where FastAPI alone needs an `app.dependency_overrides` entry per dependency function.
- **A function on several containers.** `get_user` and `current_user` are rewritten once; every app
  resolves their clients in its own container on startup, and a request gets the clients of the app it
  came to. Apps built on different containers serve the same functions, one after another or at once.
- **A router per app.** A `ClientRouter` fills clients from one container; an app that includes a
  router of another container raises `TypeError: the router fills clients from another container than
  this app`. Create the routers inside the factory.
- **`app.dependency_overrides`** belongs to one app and keeps working, also for a dependency that takes
  clients, like `current_user` above.

## Strawberry GraphQL

Strawberry's FastAPI router, `GraphQLRouter`, is an `APIRouter`, and its routes build the GraphQL
context with a FastAPI dependency. The context is then a class with a type-hinted `__init__` that takes
the clients, and resolvers read them from `info.context`:

```bash
pip install "nuke-di[fastapi]" strawberry-graphql
```

```python
# app/graphql.py
from collections.abc import AsyncIterator

import strawberry
from fastapi import FastAPI
from strawberry.fastapi import BaseContext, GraphQLRouter

from app.clients import UserService
from nuke_di.fastapi import ClientRoute, setup


class Context(BaseContext):
    # Built by FastAPI for every request, as a dependency of Strawberry's routes
    def __init__(self, users: UserService) -> None:
        super().__init__()
        self.users = users


@strawberry.type
class Query:
    @strawberry.field
    async def greeting(self, info: strawberry.Info[Context], user_id: int) -> str:
        return await info.context.users.greet(user_id)


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def greetings(self, info: strawberry.Info[Context], user_ids: list[int]) -> AsyncIterator[str]:
        for user_id in user_ids:
            yield await info.context.users.greet(user_id)


schema = strawberry.Schema(query=Query, subscription=Subscription)

app = FastAPI()
setup(app)
graphql = GraphQLRouter(schema, context_getter=Context, route_class=ClientRoute)
app.include_router(graphql, prefix="/graphql")
```

```console
$ uvicorn app.graphql:app
INFO:     Started server process [61129]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51979 - "POST /graphql HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [61129]
```

```console
$ curl localhost:8000/graphql -H 'Content-Type: application/json' -d '{"query": "{ greeting(userId: 42) }"}'
{"data":{"greeting":"Hello, user-42!"}}
```

A query, and a subscription over the websocket of the same router:

```python
# tests/test_graphql.py
from fastapi.testclient import TestClient

from app.clients import Database
from app.graphql import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_query() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        response = client.post("/graphql", json={"query": "{ greeting(userId: 1) }"})

    assert response.json() == {"data": {"greeting": "Hello, alice!"}}


def test_subscription() -> None:
    query = "subscription { greetings(userIds: [1, 2]) }"
    with (
        TestClient(app) as client,
        client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as ws,
    ):
        ws.send_json({"type": "connection_init"})
        assert ws.receive_json() == {"type": "connection_ack"}
        ws.send_json({"id": "1", "type": "subscribe", "payload": {"query": query}})
        assert ws.receive_json()["payload"] == {"data": {"greetings": "Hello, user-1!"}}
        assert ws.receive_json()["payload"] == {"data": {"greetings": "Hello, user-2!"}}
        assert ws.receive_json() == {"id": "1", "type": "complete"}
```

```console
$ pytest -q tests/test_graphql.py
..                                                                       [100%]
2 passed in 0.21s
```

The rules:

- **The context takes the clients, the resolvers take the context.** FastAPI builds a `Context` for
  every request and every websocket connection, with the connected clients of the container in it;
  `strawberry.Info[Context]` gives the resolvers its type. A resolver takes no client by type hint:
  Strawberry has no dependency injection of its own, and `info.context` is how it hands things down.
- **`route_class=ClientRoute`** makes the routes of the router fill clients, as for any `APIRouter`.
  Strawberry hands the context getter to FastAPI wrapped in a dependency of its own, and nuke-di follows
  it down to `Context`.
- **Subscriptions.** FastAPI builds the websocket route of the router without the route class, as for any
  websocket on an `APIRouter`, yet it gets a `Context` with the clients: it shares Strawberry's context
  dependency with the GET and POST routes, which the router declares first through `ClientRoute`. They
  rewrite `Context` and bring its clients into the startup of the app. A websocket endpoint of your own on
  such a router still raises `TypeError`, see [Not supported](#not-supported).
- **Another container**: `route_class=app.router.route_class`, after `setup(app, container)`.
- Strawberry's Litestar controller takes clients through `ClientPlugin`, see
  [Litestar](litestar.md#strawberry-graphql).
