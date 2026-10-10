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
   и маршрутов включённых в него роутеров — и подключил их, каждый после своих зависимостей. При
   остановке он их отключил.
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
- **Один контейнер на приложение.** Каждое приложение получает клиентов контейнера, переданного в его `setup()`,
  поэтому два приложения на двух контейнерах обслуживают одни и те же функции одновременно, например в тестах.
  Приложение, смонтированное в приложение с `setup()`, или приложение, которое обслуживает его маршруты,
  например через `include_router(api.router)`, запускающий lifespan `api`, получает клиентов, запущенных
  тем приложением.
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

## <a id="class-based-views"></a>Представления на классах

Маршруты, которым нужно одно и то же — пользователь запроса и несколько клиентов, — получают это одним
классом. Класс — это зависимость FastAPI с `__init__` с аннотациями типов, написанная так же, как
клиент, и его `__init__` принимает данные запроса и клиенты рядом друг с другом:

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

Тест подменяет клиент один раз для всех маршрутов, которые используют этот класс:

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

Правила:

- **Одно представление на запрос, один клиент на контейнер.** FastAPI строит `Account` для каждого
  запроса; `db` и `users` в нём — подключённые клиенты контейнера, одни и те же объекты во всех
  запросах. nuke-di переписал сигнатуру класса, как делает это для функции-зависимости, и не тронул его
  `__init__`: `Account(x_user_id=7, db=db, users=users)` в юнит-тесте работает как раньше. Dataclass
  работает так же, его поля и есть аргументы.
- **Представление, которому ничего не нужно от запроса, — это клиент.** `class Account(Client)`, принятый
  как `account: Account`, строится один раз на контейнер и подключается вместе с остальными. Класс
  клиента, записанный как `Annotated[Account, Depends()]`, например декорированный `@client_dataclass`,
  FastAPI вместо этого строит на каждый запрос, и его `connect()` никогда не вызывается: уберите `Depends()`.
- **`@cbv` из fastapi-utils не нужен.** Он заново объявляет маршруты на собственном обычном `APIRouter`,
  поэтому атрибут класса с типом клиента падает на `include_router()` с
  `TypeError: Database is a nuke-di client, not a pydantic type`. Класс выше делит клиенты между
  маршрутами средствами одного только FastAPI.

## <a id="an-app-per-test-container"></a>Приложение на контейнер теста

Фабрика приложения строит приложение вокруг переданного ей контейнера, поэтому каждый тест работает на
собственном контейнере, а сервер — на глобальном `DI`. Функции остаются на уровне модуля:

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

Тесты строят приложение на фикстуре `di`:

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

Правила:

- **Один `override()` на всё приложение.** `di.override(Database, FakeDatabase())` подменяет базу данных
  для зависимости `current_user`, для `UserService` и для всего остального, что принимает `Database`, —
  там, где одному FastAPI нужна отдельная запись в `app.dependency_overrides` на каждую функцию-зависимость.
- **Функция на нескольких контейнерах.** `get_user` и `current_user` переписываются один раз; каждое
  приложение при старте находит их клиенты в своём контейнере, а запрос получает клиенты того приложения,
  в которое он пришёл. Приложения на разных контейнерах обслуживают одни и те же функции — одно за другим
  или одновременно.
- **Роутер на приложение.** `ClientRouter` заполняет клиенты из одного контейнера; приложение, которое
  включает роутер другого контейнера, выбрасывает `TypeError: the router fills clients from another
  container than this app`. Создавайте роутеры внутри фабрики.
- **`app.dependency_overrides`** принадлежит одному приложению и продолжает работать, в том числе для
  зависимости, которая принимает клиенты, как `current_user` выше.

## <a id="strawberry-graphql"></a>Strawberry GraphQL

Роутер Strawberry для FastAPI, `GraphQLRouter`, — это `APIRouter`, и его маршруты строят контекст
GraphQL зависимостью FastAPI. Тогда контекст — это класс с `__init__` с аннотациями типов, который
принимает клиенты, а резолверы читают их из `info.context`:

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

Запрос и подписка через websocket того же роутера:

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

Правила:

- **Контекст принимает клиенты, резолверы принимают контекст.** FastAPI строит `Context` для каждого
  запроса и каждого websocket-соединения, с подключёнными клиентами контейнера внутри;
  `strawberry.Info[Context]` сообщает резолверам его тип. Резолвер не принимает клиент по аннотации типа:
  в Strawberry нет собственного внедрения зависимостей, и `info.context` — его способ передавать что-то вниз.
- **`route_class=ClientRoute`** заставляет маршруты роутера заполнять клиенты, как у любого `APIRouter`.
  Strawberry передаёт функцию получения контекста в FastAPI, обернув её в собственную зависимость, и
  nuke-di проходит через неё до `Context`.
- **Подписки.** FastAPI строит websocket-маршрут роутера без класса маршрута, как любой websocket на
  `APIRouter`, и всё же он получает `Context` с клиентами: он делит зависимость контекста Strawberry с
  маршрутами GET и POST, которые роутер объявляет раньше, через `ClientRoute`. Они переписывают `Context`
  и включают его клиенты в старт приложения. Собственный websocket-эндпоинт на таком роутере по-прежнему
  выбрасывает `TypeError`, см. [Что не поддерживается](#not-supported).
- **Другой контейнер**: `route_class=app.router.route_class`, после `setup(app, container)`.
- Контроллер Strawberry для Litestar принимает клиенты через `ClientPlugin`, см.
  [Litestar](litestar.md#strawberry-graphql).
