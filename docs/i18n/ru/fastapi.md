# <a id="fastapi"></a>FastAPI

[English](../../guide/fastapi.md) · **Русский** · [简体中文](../zh-CN/fastapi.md) · [Español](../es/fastapi.md) · [Português (Brasil)](../pt-BR/fastapi.md) · [日本語](../ja/fastapi.md) · [Polski](../pl/fastapi.md)

← [Документация](../README.ru.md#documentation)

Операция пути (path operation) в FastAPI получает клиент так же, как джоба, — по аннотации типа.
В обработчиках больше ничего писать не нужно: ни `Depends`, ни `inject()`.

```bash
pip install "nuke-di[fastapi]"
```

Нужен FastAPI 0.105 или новее. Примеры используют общий модуль клиентов:

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

API:

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

Что произошло:

1. `setup(app)` сделал так, что каждый маршрут, объявленный на `app` после этого вызова, заполняет
   свои аргументы-клиенты из глобального `DI`, и обернул lifespan приложения.
2. `@app.get` увидел `users: UserService` и только запомнил это; при импорте ничего не построено.
3. При старте lifespan разрешил клиенты маршрутов, которые обслуживает приложение, — его собственных
   и маршрутов включённых в него роутеров — и подключил их слой за слоем. При остановке он их отключил.
4. Запрос к `/users/42` получил подключённый `UserService`. `/me` прошёл через зависимость
   `current_user`, которая получает `db: Database` тем же способом.

Правила:

- **Где заполняются клиенты.** В аргументах операций пути, WebSocket-эндпоинтов и всех зависимостей,
  которые они используют, на любой глубине: функций и классов, используемых как `Depends(Auth)` или `Annotated[Auth, Depends()]`,
  включая `dependencies=` маршрута, его роутера, `include_router()` и приложения. Аргумент
  считается клиентом, если его аннотация типа — клиент, в том числе внутри `Annotated[UserService, ...]`
  без `Depends`. Все остальные аргументы достаются FastAPI: path, query, header, body, `Depends`.
- **Роутеры.** Создавайте их через `ClientRouter(...)`, который принимает те же аргументы, что и `APIRouter`,
  и включайте в приложение или в другой `ClientRouter`. `APIRouter(route_class=ClientRoute)`
  подходит для роутера, который не включает другие роутеры. Для другого контейнера используйте
  `setup(app, container)` и `ClientRouter(container=container)`; включение роутера другого
  контейнера сразу выбрасывает `TypeError`.
- **Вызывайте `setup(app)` до маршрутов.** Маршрут с клиентом, объявленный раньше, сразу падает
  с `TypeError`, описанным [ниже](#not-supported).
- **Только то, что обслуживает приложение.** Роутер, который приложение не включает, например
  импортированный только тестом, ничего не подключает при старте приложения.
- **Экземпляры.** Как и с `inject()`, `Client` — это один экземпляр на контейнер, а
  `NotSingletonClient` — один экземпляр на каждый объявляющий его аргумент, а не на каждый запрос.
- **Lifespan.** Собственный `lifespan=` приложения выполняется внутри: его код старта видит подключённые
  клиенты, а код остановки выполняется до их отключения. При остановке взводится `Shutdown` и
  останавливаются `BackgroundTasks`, если приложение их использует, — до отключения клиентов, как в
  воркере. `BackgroundTasks` из самого FastAPI — другой класс и клиентом не является.
- **Функция остаётся функцией.** Теперь её сигнатура показывает FastAPI `Annotated[UserService, Depends(...)]`,
  но прямой вызов с клиентом, например в юнит-тесте, работает как раньше.

**Тестирование.** Импорт приложения ничего не строит, поэтому тест подменяет клиент до того, как
`TestClient` запустит приложение, — через [`override()`](testing.md) или фикстуру `global_di`:

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

`app.dependency_overrides` продолжает работать, в том числе для функции-зависимости, которая принимает клиенты.

**WebSocket.** WebSocket-эндпоинт получает клиенты точно так же — и на `app`, и на `ClientRouter`:

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

**Клиент, который не смог подключиться**, срывает старт. Lifespan выбрасывает обычный `RuntimeError`,
причиной которого указан `ConnectError`, поскольку `SystemExit` вырвался бы за пределы event loop
сервера, а сервер сообщает об ошибке и завершается:

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

## <a id="not-supported"></a>Что не поддерживается

В этих местах клиенты не принимаются. Каждое из них при объявлении маршрута выбрасывает `TypeError` с объяснением:

| Где                                                     | Что делать вместо этого                       |
|---------------------------------------------------------|-----------------------------------------------|
| Роутер, созданный без `ClientRouter` / `ClientRoute`    | Создать его через `ClientRouter(...)`         |
| WebSocket-эндпоинт на `APIRouter(route_class=ClientRoute)` | Создать роутер через `ClientRouter(...)`    |
| Необязательный клиент, `Database \| None`               | Обычный `Database`                            |
| Связанный метод или вызываемый объект в роли эндпоинта или зависимости | Функция или класс              |

В отличие от этих случаев, маршрут роутера, включённого в обычный `APIRouter` вместо `ClientRouter`,
обнаруживают только старые версии FastAPI. На FastAPI 0.14x он объявляется, и приложение стартует, но
его запросы падают с `RuntimeError: UserService was not started with the app: include the router of its route into
the app or into a ClientRouter, not into a plain APIRouter`.

Запрос, пришедший без lifespan, например через `TestClient(app)` без `with`, получает
`RuntimeError`: `UserService is not connected: start the app with its lifespan`.
