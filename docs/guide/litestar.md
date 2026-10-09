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
