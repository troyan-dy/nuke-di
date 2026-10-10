# <a id="fastapi"></a>FastAPI

[English](../../guide/fastapi.md) · [Русский](../ru/fastapi.md) · **简体中文** · [Español](../es/fastapi.md) · [Português (Brasil)](../pt-BR/fastapi.md) · [日本語](../ja/fastapi.md) · [Polski](../pl/fastapi.md)

← [文档](../README.zh-CN.md#documentation)

FastAPI 的路径操作像 job 一样，通过类型提示接收客户端。每个处理函数都无需额外编写任何东西：
不需要 `Depends`，也不需要 `inject()`。

```bash
pip install "nuke-di[fastapi]"
```

需要 FastAPI 0.105 或更高版本。示例共用同一个客户端模块：

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

API 如下：

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

执行过程：

1. `setup(app)` 让此后在 `app` 上声明的每个路由都从全局 `DI` 中填充自己的客户端参数，
   并包装了应用的 lifespan。
2. `@app.get` 看到 `users: UserService` 后只做了记录；导入时什么也没有构建。
3. 启动时，lifespan 为应用所提供的路由（包括它自身的路由和它所包含的路由器中的路由）
   解析客户端，并连接它们，每个都在其依赖之后。关闭时则断开它们的连接。
4. 对 `/users/42` 的请求拿到的是已连接的 `UserService`。`/me` 则经过依赖项
   `current_user`，后者以同样的方式接收 `db: Database`。

规则如下：

- **在哪里填充客户端。** 在路径操作和 WebSocket 端点的参数中，以及它们用到的每个依赖项的参数中，不论嵌套多深：
  既包括函数，也包括以 `Depends(Auth)` 或 `Annotated[Auth, Depends()]` 方式使用的类，
  还包括路由、其路由器、`include_router()` 和应用上的 `dependencies=`。
  只要参数的类型提示是客户端，它就是客户端参数，即便写在不带 `Depends` 的 `Annotated[UserService, ...]`
  中也是如此。其余参数都交给 FastAPI 处理：路径参数、查询参数、请求头、请求体、`Depends`。
- **路由器。** 用 `ClientRouter(...)` 创建路由器，它接受与 `APIRouter` 相同的参数，
  然后把它包含进应用或另一个 `ClientRouter`。对于不包含其他路由器的路由器，
  `APIRouter(route_class=ClientRoute)` 也可以使用。如需使用其他容器，请调用
  `setup(app, container)` 和 `ClientRouter(container=container)`；包含属于另一个
  容器的路由器会立即抛出 `TypeError`。
- **在声明路由之前调用 `setup(app)`。** 在它之前声明、且带有客户端的路由会立即失败，
  抛出[下文](#not-supported)所述的 `TypeError`。
- **只连接应用实际提供的路由。** 应用没有包含的路由器（例如只在
  测试中导入的路由器）在应用启动时不会连接任何东西。
- **每个应用一个容器。** 每个应用获得的是传给其 `setup()` 的容器中的客户端，因此两个使用不同容器的应用
  可以同时服务相同的函数，例如在测试中。挂载到已调用 `setup()` 的应用中的应用，或者提供该应用路由的应用
  （例如通过会运行 `api` 的 lifespan 的 `include_router(api.router)`），获得的是该应用启动的客户端。
- **实例。** 与 `inject()` 一样，`Client` 在每个容器中只有一个实例，而
  `NotSingletonClient` 是每个声明它的参数一个实例，而不是每个请求一个。
- **lifespan。** 应用自己的 `lifespan=` 在内部运行：它的启动代码能看到已连接的客户端，
  它的关闭代码在客户端断开之前运行。关闭时，如果应用用到了 `Shutdown` 和
  `BackgroundTasks`，会先将 `Shutdown` 置位并停止 `BackgroundTasks`，然后才断开客户端，
  与 worker 中的行为一致。FastAPI 自带的 `BackgroundTasks` 是另一个类，不是客户端。
- **函数仍然是函数。** FastAPI 现在看到的签名是 `Annotated[UserService, Depends(...)]`，
  但直接传入客户端来调用它（例如在单元测试中）仍和以前一样可行。

**测试。** 导入应用不会构建任何东西，因此测试可以在 `TestClient`
启动应用之前，通过 [`override()`](testing.md) 或 `global_di` fixture 替换客户端：

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

`app.dependency_overrides` 依然有效，对接收客户端的依赖函数也同样适用。

**WebSocket。** WebSocket 端点以同样的方式接收客户端，无论它声明在应用上还是 `ClientRouter` 上：

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

**客户端连接失败**会导致启动失败。由于 `SystemExit` 会逃逸出服务器的事件循环，
lifespan 会从 `ConnectError` 抛出一个普通的 `RuntimeError`，服务器报告该错误
后退出：

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

## <a id="not-supported"></a>不支持的情况

以下位置不接受客户端。在声明路由时，每种情况都会抛出一个说明原因的 `TypeError`：

| 位置                                                    | 替代做法                                      |
|---------------------------------------------------------|-----------------------------------------------|
| 未使用 `ClientRouter` / `ClientRoute` 创建的路由器      | 用 `ClientRouter(...)` 创建                   |
| `APIRouter(route_class=ClientRoute)` 上的 WebSocket 端点 | 用 `ClientRouter(...)` 创建路由器             |
| 可选的客户端 `Database \| None`                         | 普通的 `Database`                             |
| 作为端点或依赖项的绑定方法或可调用对象                  | 函数或类                                      |

与上述情况不同，如果路由器被包含进普通的 `APIRouter` 而不是 `ClientRouter`，其中的路由
只有较旧的 FastAPI 能发现。在 FastAPI 0.14x 上，路由能正常声明，应用也能启动，但它的请求会失败，
报错 `RuntimeError: UserService was not started with the app: include the router of its route into the app or into a ClientRouter, not into a plain APIRouter`。

没有经过 lifespan 就到达的请求，例如通过不带 `with` 的 `TestClient(app)` 发出的请求，会得到一个
`RuntimeError`：`UserService is not connected: start the app with its lifespan`。

## <a id="class-based-views"></a>基于类的视图

多个路由需要相同的东西（请求对应的用户和几个客户端）时，可以把它们放进一个类里统一获取。
这个类是一个带类型提示 `__init__` 的 FastAPI 依赖项，写法和客户端一样，它的 `__init__`
同时接收请求数据和客户端：

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

测试只需替换一次客户端，所有使用该类的路由都会生效：

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

规则如下：

- **每个请求一个视图，每个容器一个客户端。** FastAPI 为每个请求构建一个 `Account`；其中的
  `db` 和 `users` 是容器中已连接的客户端，在所有请求中都是同一批对象。nuke-di 像处理依赖函数那样
  重写了这个类的签名，但没有改动它的 `__init__`：在单元测试中写
  `Account(x_user_id=7, db=db, users=users)` 仍和以前一样可行。dataclass 的用法相同，它的字段就是参数。
- **不需要请求中任何数据的视图就是客户端。** 以 `account: Account` 方式接收的 `class Account(Client)`
  在每个容器中只构建一次，并与其他客户端一起连接。而写成 `Annotated[Account, Depends()]` 的客户端类
  （例如用 `@client_dataclass` 装饰的类）则会由 FastAPI 为每个请求构建，它的 `connect()` 永远不会运行：
  去掉 `Depends()` 即可。
- **不需要 fastapi-utils 的 `@cbv`。** 它会在自己的普通 `APIRouter` 上重新声明路由，
  因此类型为客户端的类属性会在 `include_router()` 时失败，报错
  `TypeError: Database is a nuke-di client, not a pydantic type`。上面的类只靠 FastAPI 本身就能在
  路由之间共享客户端。

## <a id="an-app-per-test-container"></a>每个测试容器一个应用

应用工厂围绕传给它的容器构建应用，因此每个测试都运行在自己的容器上，而服务器运行在全局 `DI` 上。
函数仍然定义在模块级：

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

测试基于 `di` fixture 构建应用：

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

规则如下：

- **整个应用只需一个 `override()`。** `di.override(Database, FakeDatabase())` 会为依赖项
  `current_user`、为 `UserService` 以及其他所有接收 `Database` 的地方替换数据库；而只用 FastAPI 时，
  每个依赖函数都需要在 `app.dependency_overrides` 中单独写一条。
- **一个函数，多个容器。** `setup(app, di)` 会针对每个新应用的容器重写 `get_user` 和 `current_user`，
  依据的是函数原本写下的签名，而不是上一个应用留下的签名。
- **每个应用一个路由器。** `ClientRouter` 从一个容器中填充客户端；如果应用包含了属于另一个容器的
  路由器，会抛出 `TypeError: the router fills clients from another container than
  this app`。请在工厂内部创建路由器。
- **`app.dependency_overrides`** 属于单个应用，并且依然有效，对接收客户端的依赖项（如上面的
  `current_user`）也同样适用。
- **在每个应用启动前一刻再构建它。** 在 FastAPI 0.137 及更高版本中，被包含的路由器中的路由会在应用
  收到第一个请求时才构建，依据的是函数在那一刻的签名，也就是最后绑定的那个容器的签名。提前构建的应用
  （例如模块级的 `app = make_app(DI)`，被某个测试在其他测试已构建了各自的应用之后才导入）在访问 `/me`
  时会失败，报错 `RuntimeError: Database is not connected: start the app with its lifespan`；而同时
  运行的两个应用中，较旧的那个在这些路由上会拿到较新那个的客户端。因此服务器用 `uvicorn --factory`
  构建应用，测试则在 fixture 中逐个构建各自的应用。直接声明在应用上的路由（如 `/users/{user_id}`）
  会保留自己的容器。

## <a id="strawberry-graphql"></a>Strawberry GraphQL

Strawberry 的 FastAPI 路由器 `GraphQLRouter` 是一个 `APIRouter`，它的路由通过一个 FastAPI 依赖项
构建 GraphQL 上下文。于是上下文就是一个带类型提示 `__init__`、接收客户端的类，resolver 则从
`info.context` 中读取客户端：

```bash
pip install "nuke-di[fastapi]" strawberry-graphql
```

```python
# app/graphql.py
from collections.abc import AsyncIterator

import strawberry
from fastapi import Depends, FastAPI
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
graphql = GraphQLRouter(schema, context_getter=Context, route_class=ClientRoute, dependencies=[Depends(Context)])
app.include_router(graphql, prefix="/graphql")
```

```console
$ uvicorn app.graphql:app
INFO:     Started server process [51045]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51840 - "POST /graphql HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [51045]
```

```console
$ curl localhost:8000/graphql -H 'Content-Type: application/json' -d '{"query": "{ greeting(userId: 42) }"}'
{"data":{"greeting":"Hello, user-42!"}}
```

一个查询，以及通过同一路由器的 websocket 进行的订阅：

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
2 passed in 0.26s
```

规则如下：

- **上下文接收客户端，resolver 接收上下文。** FastAPI 为每个请求和每个 websocket 连接构建一个
  `Context`，其中是容器中已连接的客户端；`strawberry.Info[Context]` 把它的类型告诉 resolver。
  resolver 不通过类型提示接收客户端：Strawberry 没有自己的依赖注入，`info.context` 就是它向下传递
  数据的方式。
- **`route_class=ClientRoute` 和 `dependencies=[Depends(Context)]` 缺一不可。** Strawberry 把上下文
  获取函数包装进它自己的一个依赖项中，该依赖项的类型提示引用的是仅为类型检查器导入的类，因此 nuke-di
  无法顺着它找到 `Context`。把这个类列在 `dependencies=` 中，就能直接触达它；而 FastAPI 会在一个请求
  内缓存依赖项，所以 `Context` 仍然每个请求只构建一次。如果不这样做，`GraphQLRouter(...)` 会抛出
  `TypeError: UserService is a nuke-di client, not a pydantic type`。
- **订阅**运行在同一路由器的 websocket 路由上，以同样的方式获得 `Context`。
- **使用其他容器**：`route_class=ClientRouter(container=container).route_class`。
- Strawberry 的 Litestar 控制器不需要以上任何设置，参见 [Litestar](litestar.md#strawberry-graphql)。
