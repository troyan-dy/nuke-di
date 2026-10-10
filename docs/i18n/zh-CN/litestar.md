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

## <a id="differences-from-fastapi"></a>与 FastAPI 的区别

两者中的处理函数写法相同：`users: UserService`。区别来自各个框架注入依赖的方式：FastAPI 读取每个
函数签名中的 `Depends`，而 Litestar 按参数名匹配依赖项，因此 `ClientPlugin` 在应用上以参数名提供
每个客户端（[ADR-0004](../../adr/0004-litestar-clients-by-name.md)）。

| | FastAPI | Litestar |
|---|---|---|
| 设置方式 | 在路由之前调用 `setup(app)`；路由器通过 `ClientRouter` 创建 | 在 `plugins=` 中加入 `ClientPlugin()`；任意路由器或控制器均可 |
| 能看到哪些处理函数 | 在 `setup(app)` 之后声明的每个路由，无论在应用上，还是在被包含的 `ClientRouter` 或 `APIRouter(route_class=ClientRoute)` 上 | 创建应用时传入的处理函数；之后通过 `app.register()` 添加的看不到 |
| 参数名 | 自由：这里写 `users: UserService`，那里写 `users: Billing` | 整个应用中一个名称只对应一个客户端；同名的两个客户端会在创建应用时抛出 `TypeError` |
| `NotSingletonClient`（计划移除，[ADR-0006](../../adr/0006-clients-live-as-long-as-the-container.md)） | 每个参数一个实例 | 每个参数名一个实例，由所有使用该名称的处理函数共享 |
| 函数中被改动的部分 | 它的 `__signature__`；`get_type_hints()` 仍显示 `UserService` | 它的 `__annotations__`；`get_type_hints(include_extras=True)` 显示 `Annotated[UserService, Dependency(), SkipValidationMarker()]` |
| WebSocket | `@app.websocket` 端点 | `@websocket` 处理函数；WebSocket 监听器会抛出 `TypeError` |
| 应用自己的 lifespan | 在内部运行：客户端在它之前连接、在它之后断开 | 客户端在 `lifespan=` 和 `on_startup=` 之前连接，在 `on_shutdown=` 之后断开 |
| 在测试中替换依赖项 | `override()`，或 `app.dependency_overrides` | `override()`，或在某一层上声明同名依赖项，它优先于客户端 |
| 使用其他容器 | `setup(app, container)` 和 `ClientRouter(container=container)` | `ClientPlugin(container)` |

## <a id="litestar-3"></a>Litestar 3

Litestar 3 尚未发布：截至 2026-10-10，PyPI 上的最新版本是 2026-06-11 发布的 2.24.0，也没有 3.0 的
预发布版本。目前已知它的两点变化，在它正式发布之前 nuke-di 不做任何改动：

- **推断依赖将被移除。** Litestar 2.24 会对仅按名称匹配的依赖项发出警告：
  `Inferred dependencies will stop working in Litestar 3.0`。nuke-di 写入的标注
  `Annotated[UserService, Dependency(), SkipValidationMarker()]` 正是 `NamedDependency[...]` 和
  `SkipValidation[...]` 所代表的显式写法，Litestar 2.24 不会对其中任何部分发出警告。
- **计划支持按类型注入。** 2026-07-26 的 [v3 公告](https://litestar.dev/blog/v3-announcement) 计划在
  `NamedDependency` 之外加入 `TypeDependency[SomeService]`，provider 以类型为键，
  `dependencies={SomeService: provide_some_service}`，并指出这次 DI 改造是 3.0 进入 beta 之前仍待完成的功能。

有了以类型为键的 provider，`ClientPlugin` 就可以按类而不是按参数名提供每个客户端，上表中关于名称和
实例的那几行也就不复存在。是否这样做，要等 3.0 及其 API 发布后，通过重新审视 ADR-0004 来决定。
无论如何处理函数都不会改变：它们声明 `users: UserService`，如何告知 Litestar 只由插件决定。

## <a id="strawberry-graphql"></a>Strawberry GraphQL

Strawberry 的 Litestar 控制器通过一个 Litestar 依赖项构建 GraphQL 上下文，因此上下文获取函数可以像
其他依赖项一样接收客户端，resolver 则从 `info.context` 中读取它们：

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

规则如下：

- **上下文获取函数是一个函数。** Strawberry 的 Litestar `BaseContext` 是一个 msgspec `Struct`；
  如果直接写成 `context_getter=Context`，每个请求都会因 `msgspec.ValidationError` 而失败。
- **订阅**运行在同一控制器的 websocket 处理函数上，`ClientPlugin` 同样能看到它，并从同一个获取函数
  得到上下文。
- **弃用警告来自 Strawberry。** 在 Litestar 2.24 上，Strawberry 0.332 的控制器仅按名称声明了自己的
  依赖项 `custom_context`、`context`、`context_ws`、`root_value` 和 `response`，Litestar 会对每一个
  发出警告，因此测试要带上 `-W ignore::DeprecationWarning` 运行。`get_context` 的参数 `users` 不会
  引发任何警告。
- 不需要其他任何设置：Litestar 会把控制器的依赖项原样交给 `ClientPlugin`。在 FastAPI 上，路由器要
  传入 `route_class=ClientRoute`，参见 [FastAPI](fastapi.md#strawberry-graphql)。
