# <a id="starlette-quart-and-any-asgi-app"></a>Starlette, Quart и любое ASGI-приложение

[English](../../guide/asgi.md) · **Русский** · [简体中文](../zh-CN/asgi.md) · [Español](../es/asgi.md) · [Português (Brasil)](../pt-BR/asgi.md) · [日本語](../ja/asgi.md) · [Polski](../pl/asgi.md)

← [Документация](../README.ru.md#documentation)

Фреймворк без внедрения зависимостей, такой как Starlette или Quart, не умеет заполнять аргументы
обработчика по аннотации типа. Остальное берёт на себя `nuke_di.asgi.lifespan()`: это lifespan
приложения, который подключает перечисленные в нём клиенты при старте приложения и отключает их при
остановке, а обработчик получает клиент у него через `get()`. Модуль не импортирует ни одного фреймворка
и не требует extra:

```bash
pip install nuke-di
```

- [Starlette](#starlette)
- [Собственный lifespan приложения](#the-apps-own-lifespan)
- [Тестирование](#testing)
- [Quart](#quart)
- [aiohttp](#aiohttp)
- [ASGI-приложение без фреймворка](#a-plain-asgi-app)
- [Ошибки](#errors)

## <a id="starlette"></a>Starlette

С клиентами из примеров для [FastAPI](fastapi.md):

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

Правила:

- **Список явный.** Таблицы маршрутов, в которой можно было бы найти клиенты, нет, поэтому
  `lifespan(container, *clients)` называет каждый клиент, который берёт обработчик. `clients.get(Database)`
  работает, только если `Database` есть в списке, хотя он и так подключается как зависимость
  `UserService`: обработчик, который на это полагается, сломается в тот день, когда `UserService`
  перестанет от него зависеть.
- **`get()` типизирован.** `clients.get(UserService)` возвращает `UserService` для mypy и pyright —
  в отличие от атрибута `request.state`. Это обычный метод, поэтому он работает в обработчике,
  websocket-эндпоинте, middleware или фоновой задаче, а модуль, в котором лежит `clients`, обработчики
  импортируют как любой другой.
- **Разрешение при старте.** Создание `clients` ничего не разрешает; каждый старт заново разрешает
  перечисленные клиенты, поэтому `override()` до запуска приложения подменяет клиент, а второй старт
  получает новые клиенты.
- **Остановка.** При остановке взводится `Shutdown`, останавливаются `BackgroundTasks`, и только потом
  клиенты отключаются — в том же порядке, что и в [воркере](workers-and-jobs.md).
- **Одно приложение за раз.** Контейнер подключается один раз: второе приложение, запущенное на том же
  контейнере, пока работает первое, падает при старте.
- **И FastAPI тоже.** Приложение FastAPI, которое оставляет сигнатуры своих маршрутов как есть, точно так
  же передаёт `FastAPI(lifespan=clients)`; с `nuke_di.fastapi.setup()` его маршруты вместо этого получают
  клиенты по аннотации типа, см. [FastAPI](fastapi.md).

## <a id="the-apps-own-lifespan"></a>Собственный lifespan приложения

`clients(app)` — асинхронный контекстный менеджер, поэтому собственный lifespan приложения сначала входит
в него, а свой код старта и остановки выполняет внутри, когда клиенты уже подключены. Отдаёт он, как
обычно, состояние приложения:

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

## <a id="testing"></a>Тестирование

Тест подменяет клиент до того, как `TestClient` запустит приложение:

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

`TestClient(app)` без `with` отправляет запросы, не запуская lifespan, и обработчик об этом сообщает:

```console
$ python -c "from starlette.testclient import TestClient; from app.starlette_api import app; TestClient(app).get('/users/1')"
Traceback (most recent call last):
  ...
RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` in Starlette or `async with app.test_app()` in Quart
```

## <a id="quart"></a>Quart

У Quart нет аргумента `lifespan=`: при старте он выполняет хуки `before_serving`, а при остановке —
хуки `after_serving`, каждый вид в порядке регистрации, и пропускает остальные, если один из них упал.
Подкласс `Quart` подключает клиенты вокруг всех этих хуков, так что любой хук приложения может
пользоваться клиентами, а упавший хук всё равно оставляет их отключёнными:

```python
# app/quart_api.py
from contextlib import AsyncExitStack

from quart import Quart, request

from app.clients import Database, UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService, Database)
running = AsyncExitStack()


class ClientsQuart(Quart):
    """
    Connects the clients before every before_serving hook and disconnects them after every after_serving
    hook, also when one of the hooks fails.
    """

    async def startup(self) -> None:
        await running.enter_async_context(clients(self))
        try:
            await super().startup()
        except BaseException:
            await running.aclose()
            raise

    async def shutdown(self) -> None:
        try:
            await super().shutdown()
        finally:
            await running.aclose()


app = ClientsQuart(__name__)


@app.before_serving
async def warm_up() -> None:
    print("warm-up:", await clients.get(UserService).greet(0))


@app.after_serving
async def goodbye() -> None:
    print("goodbye:", await clients.get(UserService).greet(1))


@app.get("/users/<int:user_id>")
async def get_user(user_id: int) -> str:
    return await clients.get(UserService).greet(user_id)


@app.get("/me")
async def me() -> str:
    return await clients.get(Database).fetch_user(int(request.headers["X-User-Id"]))
```

```console
$ uvicorn app.quart_api:app
INFO:     Started server process [72984]
INFO:     Waiting for application startup.
database: connected
warm-up: Hello, user-0!
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:57604 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:57606 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
goodbye: Hello, user-1!
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [72984]
```

Тест запускает приложение через `test_app()`:

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
1 passed in 0.13s
```

Почему не два хука приложения, один подключает, другой отключает: каждый из них выполнялся бы в свою
очередь среди собственных хуков приложения. `disconnect` в `after_serving` выполняется раньше хуков
`after_serving`, зарегистрированных после него, и те застают клиенты уже отключёнными; хук
`before_serving`, упавший после того, который подключил клиенты, оставляет контейнер подключённым, ведь
тогда Quart не вызывает ни одного хука `after_serving`, и следующий старт падает с `the container is
already connected`. `Quart.startup()` и `Quart.shutdown()` выполняют все хуки, поэтому подкласс
подключает клиенты до первого и отключает после последнего, какой бы из них ни упал. С `while_serving` из
Quart код вышел бы короче, но Quart создаёт его генератор один раз, при регистрации, поэтому приложение
смогло бы стартовать лишь раз за процесс, и второй тест, который его запускает, упал бы.

## <a id="aiohttp"></a>aiohttp

aiohttp 3.14 принимает в `cleanup_ctx` асинхронный контекстный менеджер, а `clients` — как раз такой:

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

Более старый aiohttp вместо этого принимает асинхронный генератор:

```python
from collections.abc import AsyncIterator


async def run_clients(app: web.Application) -> AsyncIterator[None]:
    async with clients(app):
        yield


app.cleanup_ctx.append(run_clients)
```

## <a id="a-plain-asgi-app"></a>ASGI-приложение без фреймворка

ASGI-приложение без фреймворка само отвечает на lifespan-сообщения сервера. Там `clients()` вызывается
без аргумента:

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

## <a id="errors"></a>Ошибки

| Когда | Что выбрасывается |
|---|---|
| Обработчик выполняется без lifespan приложения или после его остановки | ``RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` in Starlette or `async with app.test_app()` in Quart `` |
| `get()` клиента, которого нет в списке | ``RuntimeError: Database is not a client of this lifespan: list it in `lifespan(container, ...)` `` |
| `connect()` клиента падает | `RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused`; сервер сообщает о неудачном старте и завершается, а контейнер остаётся очищенным, как после `flush()` |
| Контейнер уже подключён, например другим приложением | `RuntimeError: nuke-di clients failed to start: the container is already connected` |
| `lifespan(Database)` без контейнера | `TypeError: lifespan() takes the container first, then the clients, e.g. lifespan(DI, Database); got <class 'app.clients.Database'>` |
| `lifespan(DI, "Database")` — не класс клиента | `TypeError: 'Database' is not a client: subclass Client or NotSingletonClient` |

Под uvicorn, с `Database`, чей `connect()` выбрасывает `OSError("connection refused")`:

```console
$ uvicorn app.broken_api:app
INFO:     Started server process [44319]
INFO:     Waiting for application startup.
Database.connect() raised OSError: connection refused
...
RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused

ERROR:    Application startup failed. Exiting.
```
