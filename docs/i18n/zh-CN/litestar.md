# <a id="litestar"></a>Litestar

[English](../../guide/litestar.md) · [Русский](../ru/litestar.md) · **简体中文** · [Español](../es/litestar.md) · [Português (Brasil)](../pt-BR/litestar.md) · [日本語](../ja/litestar.md) · [Polski](../pl/litestar.md)

← [文档](../README.zh-CN.md#documentation)

Litestar 的路由处理函数同样通过类型提示接收客户端，借助一个插件实现：

```bash
pip install "nuke-di[litestar]"
```

需要 Litestar 2.15 或更高版本。使用 [FastAPI](fastapi.md) 示例中的客户端：

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

`ClientPlugin()` 在 `get_user` 中找到了 `users: UserService`，在依赖项 `current_user` 中找到了
`db: Database`，把两者作为依赖项提供给 Litestar，并在启动时连接它们。

规则如下：

- **在哪里填充客户端。** 在创建应用时传入的 HTTP 处理函数和 `@websocket` 处理函数的参数中，
  包括任意深度的路由器和控制器中的处理函数，以及在应用、路由器、控制器或处理函数上声明的
  每个依赖项的参数中：既包括函数，也包括类。
- **按名称提供。** Litestar 按参数名提供依赖项，因此 nuke-di 在应用上以参数名提供每个客户端参数。
  在整个应用中，一个名称只对应一个客户端：如果一个处理函数中写 `users: UserService`，另一个中写
  `users: Billing`，创建应用时就会抛出 `TypeError`。应用、路由器、控制器或处理函数声明的同名
  依赖项优先于客户端。
- **实例。** `Client` 在每个容器中只有一个实例；`NotSingletonClient` 是每个参数名一个实例。
- **lifespan。** 客户端在应用自己的 `lifespan=` 和 `on_startup=` 运行之前连接，在它的
  `on_shutdown=` 钩子之后断开，Litestar 最后才调用这些钩子。`Shutdown` 和
  `BackgroundTasks` 的行为与 [FastAPI](fastapi.md) 中相同。
- **函数仍然是函数。** 它的客户端参数现在标注为 Litestar 的显式依赖项，且不校验其值：
  `Annotated[UserService, Dependency(), SkipValidationMarker()]`，这正是 Litestar 2.23 所要求的
  写法，用来取代仅按名称匹配的依赖项。直接调用该函数仍和以前一样可行。
- **插件。** 把 `ClientPlugin()` 放在所有会添加路由处理函数的插件之后：轮到它时，它看到的是应用
  此刻已有的处理函数。
- **使用其他容器。** `ClientPlugin(container)`。

**测试。** 与 FastAPI 一样，测试在 `TestClient` 启动应用之前替换客户端：

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

**不支持的情况。** WebSocket 监听器，即 `@websocket_listener` 或 `WebsocketListener` 类，不接受
客户端：Litestar 在声明它时就读取了其签名，早于插件看到它，因此应用会抛出 `TypeError`，并建议
改用 `@websocket` 处理函数。如果客户端参数使用了 Litestar 保留的名称，例如 `state` 或 `request`，
同样会抛出 `TypeError`。在应用创建之后通过 `app.register()` 注册的处理函数，插件看不到。
