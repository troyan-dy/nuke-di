# Litestar

**English** · [Русский](../i18n/ru/litestar.md) · [简体中文](../i18n/zh-CN/litestar.md) · [Español](../i18n/es/litestar.md) · [Português (Brasil)](../i18n/pt-BR/litestar.md) · [日本語](../i18n/ja/litestar.md) · [Polski](../i18n/pl/litestar.md)

← [Documentation](../../README.md#documentation)

A Litestar route handler takes a client by its type hint too, through a plugin:

```bash
pip install "nuke-di[litestar]"
```

Requires Litestar 2.15 or newer. With the clients of the [FastAPI](fastapi.md) examples:

```python
# app/litestar_api.py
from typing import Annotated

from litestar import Litestar, get
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, HeaderParameter

from app.clients import Database, UserService
from nuke_di.litestar import ClientPlugin


@get("/users/{user_id:int}")
async def get_user(user_id: FromPath[int], users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, HeaderParameter(name="X-User-Id")], db: Database) -> str:
    return await db.fetch_user(x_user_id)


@get("/me", dependencies={"user": Provide(current_user)})
async def me(user: NamedDependency[str]) -> str:
    return user


app = Litestar([get_user, me], plugins=[ClientPlugin()])
```

```console
$ uvicorn app.litestar_api:app
INFO:     Started server process [6801]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51940 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51942 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [6801]
```

```console
$ curl localhost:8000/users/42
Hello, user-42!
$ curl localhost:8000/me -H "X-User-Id: 7"
user-7
```

`ClientPlugin()` found `users: UserService` in `get_user` and `db: Database` in the dependency
`current_user`, provided both to Litestar as dependencies, and connected them on startup.

The rules:

- **Where clients are filled.** In the arguments of the HTTP and `@websocket` handlers the app is
  created with, including those of routers and controllers at any depth, and of every dependency
  declared on the app, a router, a controller or a handler: functions and classes.
- **By name.** Litestar provides dependencies by argument name, so nuke-di provides every client
  argument under its name, on the app. One name means one client in the whole app: `users: UserService`
  in one handler and `users: Billing` in another raise `TypeError` when the app is created. A
  dependency of the same name declared by the app, a router, a controller or a handler wins over
  the client.
- **Instances.** A `Client` is one instance per container; a `NotSingletonClient` is one instance per
  argument name.
- **Lifespan.** The clients connect before the app's own `lifespan=` and `on_startup=` run, and
  disconnect after its `on_shutdown=` hooks, which Litestar calls last. `Shutdown` and
  `BackgroundTasks` behave as in [FastAPI](fastapi.md).
- **The function stays a function.** Its client arguments are now annotated as explicit Litestar
  dependencies whose value is not validated, `Annotated[UserService, Dependency(), SkipValidationMarker()]`,
  which is what Litestar 2.23 asks for instead of a dependency matched by name only. Calling the function
  directly works as before.
- **Plugins.** Put `ClientPlugin()` after any plugin that adds route handlers: it sees the handlers the
  app has when its turn comes.
- **Another container.** `ClientPlugin(container)`.

**Testing.** As with FastAPI, a test replaces a client before `TestClient` starts the app:

```python
# tests/test_litestar_api.py
from litestar.testing import TestClient

from app.clients import Database
from app.litestar_api import app
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
$ pytest -q tests/test_litestar_api.py
.                                                                        [100%]
1 passed in 0.23s
```

**Not supported.** A websocket listener, `@websocket_listener` or a `WebsocketListener` class, takes no
clients: Litestar reads its signature when it is declared, before the plugin sees it, so the app
raises `TypeError` and names a `@websocket` handler instead. A client argument under a name Litestar
reserves, such as `state` or `request`, raises `TypeError` too. A handler registered after the app is
created, with `app.register()`, is not seen.

## Differences from FastAPI

A handler is written the same way in both, `users: UserService`. What differs comes from how each
framework injects: FastAPI reads a `Depends` in the signature of every function, Litestar matches a
dependency by the argument's name, so `ClientPlugin` provides every client under its argument name, on
the app ([ADR-0004](../adr/0004-litestar-clients-by-name.md)).

| | FastAPI | Litestar |
|---|---|---|
| Set up | `setup(app)` before the routes; routers through `ClientRouter` | `ClientPlugin()` in `plugins=`; any router or controller |
| Handlers seen | Every route declared after `setup(app)`, on the app or on an included `ClientRouter` or `APIRouter(route_class=ClientRoute)` | The handlers the app is created with; one added later with `app.register()` is not |
| Argument names | Free: `users: UserService` here and `users: Billing` there | One name, one client in the whole app; two clients under one name raise `TypeError` when the app is created |
| A `NotSingletonClient` (slated for removal, [ADR-0006](../adr/0006-clients-live-as-long-as-the-container.md)) | One instance per argument | One instance per argument name, shared by every handler that uses the name |
| What changes in the function | Its `__signature__`; `get_type_hints()` still shows `UserService` | Its `__annotations__`; `get_type_hints(include_extras=True)` shows `Annotated[UserService, Dependency(), SkipValidationMarker()]` |
| Websockets | `@app.websocket` endpoints | `@websocket` handlers; a websocket listener raises `TypeError` |
| The app's own lifespan | Runs inside: the clients connect before it and disconnect after it | The clients connect before `lifespan=` and `on_startup=`, disconnect after `on_shutdown=` |
| Replacing a dependency in a test | `override()`, or `app.dependency_overrides` | `override()`, or a dependency of the same name on a layer, which wins over the client |
| Another container | `setup(app, container)` and `ClientRouter(container=container)` | `ClientPlugin(container)` |

## Litestar 3

Litestar 3 is not released yet: on 2026-10-10 the latest release on PyPI is 2.24.0, of 2026-06-11, and
there is no 3.0 pre-release. Two things about it are known, and nuke-di changes nothing until it ships:

- **Inferred dependencies go.** Litestar 2.24 warns about a dependency matched by its name alone:
  `Inferred dependencies will stop working in Litestar 3.0`. The annotation nuke-di writes,
  `Annotated[UserService, Dependency(), SkipValidationMarker()]`, is what `NamedDependency[...]` and
  `SkipValidation[...]` stand for, the explicit form, and Litestar 2.24 warns about none of it.
- **Injection by type is planned.** The [v3 announcement](https://litestar.dev/blog/v3-announcement)
  of 2026-07-26 plans `TypeDependency[SomeService]` next to `NamedDependency`, with the provider keyed by
  the type, `dependencies={SomeService: provide_some_service}`, and names this DI overhaul as the feature
  that still keeps 3.0 from its beta.

With providers keyed by type, `ClientPlugin` could provide every client under its class instead of
its argument name, and the rows of the table above about names and instances would go. Whether it does
is decided when 3.0 and its API are out, by revisiting ADR-0004. Handlers do not change either way: they
declare `users: UserService`, and only the plugin decides how Litestar is told about it.

## Strawberry GraphQL

Strawberry's Litestar controller builds the GraphQL context with a Litestar dependency, so a context
getter takes clients like any other dependency, and resolvers read them from `info.context`:

```bash
pip install "nuke-di[litestar]" strawberry-graphql
```

```python
# app/litestar_graphql.py
import strawberry
from litestar import Litestar
from strawberry.litestar import BaseContext, make_graphql_controller

from app.clients import UserService
from nuke_di.litestar import ClientPlugin


class Context(BaseContext, kw_only=True):
    users: UserService


async def get_context(users: UserService) -> Context:
    return Context(users=users)


@strawberry.type
class Query:
    @strawberry.field
    async def greeting(self, info: strawberry.Info[Context], user_id: int) -> str:
        return await info.context.users.greet(user_id)


schema = strawberry.Schema(query=Query)
GraphQLController = make_graphql_controller(schema, path="/graphql", context_getter=get_context)
app = Litestar([GraphQLController], plugins=[ClientPlugin()])
```

```console
$ uvicorn app.litestar_graphql:app
INFO:     Started server process [56803]
INFO:     Waiting for application startup.
database: connected
INFO - 2026-10-10 18:40:33,849 - nuke_di.core - core - Connected 2 clients in 0.00s (slowest: Database 0.00s, UserService 0.00s)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:53760 - "POST /graphql HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [56803]
```

```console
$ curl localhost:8000/graphql -H 'Content-Type: application/json' -d '{"query": "{ greeting(userId: 42) }"}'
{"data":{"greeting":"Hello, user-42!"}}
```

```python
# tests/test_litestar_graphql.py
from litestar.testing import TestClient

from app.clients import Database
from app.litestar_graphql import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_query() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        response = client.post("/graphql", json={"query": "{ greeting(userId: 1) }"})

    assert response.json() == {"data": {"greeting": "Hello, alice!"}}
```

```console
$ pytest -q -W ignore::DeprecationWarning tests/test_litestar_graphql.py
.                                                                        [100%]
1 passed in 0.37s
```

The rules:

- **The context getter is a function.** Strawberry's Litestar `BaseContext` is a msgspec `Struct`;
  given as `context_getter=Context` itself, it fails every request with a `msgspec.ValidationError`.
- **Subscriptions** run on the websocket handler of the same controller, which `ClientPlugin` sees
  too, and get the context from the same getter.
- **The deprecation warnings are Strawberry's.** On Litestar 2.24 the controller of Strawberry 0.332
  declares its own dependencies, `custom_context`, `context`, `context_ws`, `root_value` and `response`,
  by name alone, and Litestar warns about each, which is why the test runs with `-W ignore::DeprecationWarning`. The argument `users` of
  `get_context` raises none.
- Nothing else is needed: Litestar hands the dependencies of the controller to `ClientPlugin` as they
  are. On FastAPI the router takes `route_class=ClientRoute`, see [FastAPI](fastapi.md#strawberry-graphql).
