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
   解析客户端，并逐层连接它们。关闭时则断开它们的连接。
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
