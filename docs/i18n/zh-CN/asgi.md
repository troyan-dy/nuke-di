# <a id="starlette-quart-and-any-asgi-app"></a>Starlette、Quart 及任意 ASGI 应用

[English](../../guide/asgi.md) · [Русский](../ru/asgi.md) · **简体中文** · [Español](../es/asgi.md) · [Português (Brasil)](../pt-BR/asgi.md) · [日本語](../ja/asgi.md) · [Polski](../pl/asgi.md)

← [文档](../README.zh-CN.md#documentation)

Starlette、Quart 这类没有依赖注入的框架，无法按类型提示填充处理函数的参数。
其余的部分由 `nuke_di.asgi.lifespan()` 负责：它就是应用的 lifespan，在应用启动时连接它所列出的客户端，
在应用停止时断开它们，处理函数则通过 `get()` 向它索取客户端。它不导入任何框架，也不需要安装额外依赖：

```bash
pip install nuke-di
```

- [Starlette](#starlette)
- [应用自己的 lifespan](#the-apps-own-lifespan)
- [测试](#testing)
- [Quart](#quart)
- [aiohttp](#aiohttp)
- [纯 ASGI 应用](#a-plain-asgi-app)
- [错误](#errors)

## <a id="starlette"></a>Starlette

使用 [FastAPI](fastapi.md) 示例中的客户端：

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

规则如下：

- **列表是显式的。** 没有可供查找客户端的路由表，因此 `lifespan(container,
  *clients)` 要列出处理函数用到的每一个客户端。只有列出了 `Database`，`clients.get(Database)` 才能工作，
  即便它作为 `UserService` 的依赖无论如何都会被连接：依赖这一点的处理函数，
  会在 `UserService` 不再依赖它的那天出错。
- **`get()` 是有类型的。** 与 `request.state` 上的属性不同，`clients.get(UserService)` 在 mypy 和 pyright
  看来返回的就是 `UserService`。它是一个普通方法，因此在处理函数、WebSocket 端点、
  中间件或后台任务中都能使用，而持有 `clients` 的模块也像其他模块一样被处理函数导入。
- **在启动时解析。** 创建 `clients` 时什么也不解析；每次启动都会重新解析列出的客户端，
  因此在应用启动之前调用 `override()` 就能替换其中某一个，第二次启动拿到的是全新的客户端。
- **关闭。** 关闭时先将 `Shutdown` 置位，停止 `BackgroundTasks`，然后断开客户端，
  顺序与 [worker](workers-and-jobs.md) 中相同。
- **同一时间只有一个应用。** 一个容器只连接一次：第一个应用仍在运行时，
  在同一容器上启动的第二个应用会启动失败。
- **FastAPI 也适用。** 保持路由签名原样的 FastAPI 应用，可以用同样的方式传入
  `FastAPI(lifespan=clients)`；而使用 `nuke_di.fastapi.setup()` 时，它的路由改为按类型提示接收客户端，
  参见 [FastAPI](fastapi.md)。

## <a id="the-apps-own-lifespan"></a>应用自己的 lifespan

`clients(app)` 是一个异步上下文管理器，因此应用自己的 lifespan 先进入它，再在其中运行自己的启动和关闭代码，
此时客户端都已连接。它 yield 出的值照常是应用的状态：

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

## <a id="testing"></a>测试

测试在 `TestClient` 启动应用之前替换客户端：

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

不带 `with` 的 `TestClient(app)` 发送请求时不会运行 lifespan，处理函数会明确指出这一点：

```console
$ python -c "from starlette.testclient import TestClient; from app.starlette_api import app; TestClient(app).get('/users/1')"
Traceback (most recent call last):
  ...
RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)`
```

## <a id="quart"></a>Quart

Quart 没有 `lifespan=` 参数：它在启动时运行 `before_serving` 钩子，在关闭时运行 `after_serving` 钩子。
一个 `AsyncExitStack` 让客户端在两者之间保持连接：

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

测试用 `test_app()` 启动应用：

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

Quart 按注册顺序调用同一类钩子：`connect` 排在你自己的
`before_serving` 钩子之前，因此它们能看到客户端；`disconnect` 则排在你自己的 `after_serving` 钩子之后。
用 Quart 的 `while_serving` 写起来会更短，但它在注册时就只创建一次生成器，于是应用
在每个进程中只能启动一次，第二个启动它的测试就会失败。

## <a id="aiohttp"></a>aiohttp

aiohttp 3.14 的 `cleanup_ctx` 接受异步上下文管理器，而 `clients` 正是其中之一：

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

较旧的 aiohttp 则需要传入一个异步生成器：

```python
from collections.abc import AsyncIterator


async def run_clients(app: web.Application) -> AsyncIterator[None]:
    async with clients(app):
        yield


app.cleanup_ctx.append(run_clients)
```

## <a id="a-plain-asgi-app"></a>纯 ASGI 应用

不使用框架的 ASGI 应用要自己响应服务器发来的 lifespan 消息。这时 `clients()`
不接受参数：

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

## <a id="errors"></a>错误

| 情形 | 抛出的异常 |
|---|---|
| 处理函数在没有应用 lifespan 的情况下运行，或在其停止之后运行 | ``RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` `` |
| 对列表中没有的客户端调用 `get()` | ``RuntimeError: Database is not a client of this lifespan: list it in `lifespan(container, ...)` `` |
| 某个客户端的 `connect()` 失败 | `RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused`；服务器报告启动失败并退出，容器处于已 `flush()` 的状态 |
| 容器已经被连接，例如被另一个应用连接 | `RuntimeError: nuke-di clients failed to start: the container is already connected` |
| `lifespan(Database)`，漏掉了容器 | `TypeError: lifespan() takes the container first, then the clients, e.g. lifespan(DI, Database); got <class 'app.clients.Database'>` |
| `lifespan(DI, "Database")`，不是客户端类 | `TypeError: 'Database' is not a client: subclass Client or NotSingletonClient` |

在 uvicorn 下，使用一个 `connect()` 会抛出 `OSError("connection refused")` 的 `Database`：

```console
$ uvicorn app.broken_api:app
INFO:     Started server process [44319]
INFO:     Waiting for application startup.
Database.connect() raised OSError: connection refused
...
RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused

ERROR:    Application startup failed. Exiting.
```
