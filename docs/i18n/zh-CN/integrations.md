# <a id="writing-an-integration"></a>编写集成

[English](../../guide/integrations.md) · [Русский](../ru/integrations.md) · **简体中文** · [Español](../es/integrations.md) · [Português (Brasil)](../pt-BR/integrations.md) · [日本語](../ja/integrations.md) · [Polski](../pl/integrations.md)

← [文档](../README.zh-CN.md#documentation)

与框架的集成要做两件事：框架的处理函数通过框架自身的依赖注入，按类型提示接收客户端；容器在应用启动时连接，
在应用停止时断开。[FastAPI](fastapi.md)、[Litestar](litestar.md) 和 [FastStream](faststream.md) 的集成
都基于 `nuke_di.integration` 构建；与其他框架的集成，除了它和公开 API 之外，不需要 nuke-di 的任何其他东西。

| 名称 | 作用 |
|---|---|
| `Framework(name, not_started, not_connected)` | 客户端缺失时告诉该框架用户的消息；消息中的 `{client}` 是客户端的类名。 |
| `DependsFramework(..., depends, make_depends, per_container=True)` | 通过 `Depends(...)` 标记注入的框架：`depends` 是其标记的类，`make_depends` 为一个函数构建标记。 |
| `bind(call, container, framework)` | 重写处理函数、依赖函数或依赖类的签名，以及它所用依赖的签名：每个客户端参数都变成 `Annotated[Client, Depends(...)]`。返回每个客户端的 `Binding`。无法求值的类型提示（例如在 `TYPE_CHECKING` 下导入的名称）保持原样，其余参数照常重写。 |
| `Binding(cls, container, framework)` | 一个客户端参数：`cls` 是客户端的类，`instance` 是 `running()` 解析出的客户端，启动之前和关闭之后为 `None`。`get()` 返回它，否则抛出 `not_started` / `not_connected`。它们由 `bind()` 创建；不使用 `Depends` 的集成则自己为每个客户端参数创建一个。 |
| `running(container, bindings)` | 一个异步上下文管理器：解析 `bindings` 中的客户端，连接容器；退出时设置 `Shutdown`、停止 `BackgroundTasks` 并断开连接。`ConnectError` 或 `InitializeDependencyError` 会变成 `RuntimeError`，服务器会将其报告为启动失败；客户端树的错误（例如循环依赖）则原样抛出。 |
| `wrap_lifespan(original, container, bindings)` | 一个在 `running()` 内运行应用自己的 `original` lifespan 的 lifespan；`bindings` 在启动时调用，因此在 `setup()` 之后声明的处理函数也能被找到。 |
| `client_of(hint, *markers)` | 类型提示所请求的客户端，或 `None`：`Client`，或不带任何 `markers` 的 `Annotated[Client, ...]`。 |
| `unique(bindings)` | 去掉重复项的 `bindings`：同一个依赖常常可以从多个处理函数到达。 |

## <a id="a-framework-with-depends"></a>带 `Depends` 的框架

FastAPI 和 FastStream（通过 fast-depends）读取处理函数的 `inspect.signature()`，并调用每个 `Depends(...)`
标记中的依赖，标记以同样方式工作的任何框架也是如此。对于这些框架，`bind()` 把 `users: UserService` 替换为
`users: Annotated[UserService, Depends(binding.get)]`，其余的交给框架处理。仅用公开工具包写成的
FastStream 集成就是下面这个模块：

```python
# myapp/faststream_di.py
from typing import Any

from faststream import Depends, FastStream

from nuke_di import DI, Dependencies
from nuke_di.integration import Binding, DependsFramework, bind, unique, wrap_lifespan


def _noop() -> None: ...


FRAMEWORK = DependsFramework(
    name="FastStream",
    # The class of FastStream's markers, and the function that builds one
    depends=type(Depends(_noop)),
    make_depends=Depends,
    not_started="{client} was not started with the app: declare its subscriber before the app starts",
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`",
    # FastStream builds a subscriber on every start: a function is bound once, whatever the container
    per_container=False,
)


def setup(app: FastStream, container: Dependencies = DI) -> None:
    # Connect the container around the app's own lifespan, with the clients found on startup
    app.lifespan_context = wrap_lifespan(app.lifespan_context, container, lambda: _bindings(app, container))
    for broker in app.brokers:
        # Rewrite a subscriber's function whenever FastStream builds the subscriber
        config = broker.config.fd_config
        config.call_decorators = (*config.call_decorators, _Rewrite(container))


class _Rewrite:
    def __init__(self, container: Dependencies) -> None:
        self.container = container

    def __call__(self, call: Any) -> Any:
        bind(call, self.container, FRAMEWORK)
        return call


def _bindings(app: FastStream, container: Dependencies) -> list[Binding]:
    # The clients of every subscriber and of the dependencies it declares, each once
    bindings: list[Binding] = []
    for broker in app.brokers:
        for subscriber in broker.subscribers:
            for item in subscriber.calls:
                bindings += bind(item.handler._declared_call, container, FRAMEWORK)
                for depends in item.dependencies:
                    bindings += bind(depends.dependency, container, FRAMEWORK)
    return unique(bindings)
```

有三件事是框架特有的，每个集成都要在该框架的内部实现中找到它们：

- **何时读取签名。** `bind()` 必须在框架读取处理函数的签名之前运行。FastAPI 在声明路由时读取签名，
  因此 `nuke_di.fastapi` 在它的路由类中绑定；FastStream 在构建订阅者时读取签名，而且每次启动都会构建，
  因此上面的模块在 `call_decorators` 钩子中绑定。
- **处理函数在哪里。** 启动时，`wrap_lifespan()` 调用 `bindings()` 来得知要解析哪些客户端：集成遍历
  应用的路由、订阅者或任务，绑定每一个，并返回这些 `Binding`。应用中没有任何地方用到的客户端不会被连接。
- **lifespan 在哪里。** `wrap_lifespan()` 替换应用的 lifespan；应用自己的 lifespan 在其内部运行，
  因此它的启动代码可以使用客户端。

真正的 `nuke_di.faststream` 补上了示例省略的部分：FastStream 0.6、broker 和路由器的依赖，以及当多个应用
共享同一个 broker 时，每个 broker 只装一个重写钩子。

### <a id="per-container"></a>`per_container`

`bind()` 就地重写函数，并记住它所绑定的容器。当 `per_container=True`（默认值）时，一个已绑定到某个容器、
又为另一个容器再次声明的函数会被重新绑定，因此每个应用都保留自己捕获的绑定。这只适用于只在声明处理函数时
读取一次其签名、之后再也不读取的框架：为第二个容器重写的签名，会在框架下一次读取时影响到第一个应用。
nuke-di 自带的集成都已不再使用这种方式：FastAPI 在 0.136 之前正是这样读取签名的。

当 `per_container=False`（FastStream、FastAPI）时，函数只绑定一次，与容器无关，每个启动的应用都解析同样的
`Binding`。FastStream 在每次启动时构建订阅者，在测试 broker 下甚至在应用的 lifespan 运行之前就构建；
FastAPI 0.137 及更高版本则在应用收到第一个请求时才构建被包含路由器的路由。无论哪种情况，签名都不能在不同
应用之间变化。代价是：共享同一个处理函数的两个应用只能依次运行，第二个会抛出
`RuntimeError: ... is filled for another app that is running`。如果框架可能在第一个应用启动之后再次读取
签名，就选择 `False`。`nuke_di.fastapi` 避免了这个代价，因为 FastAPI 的依赖可以接收请求：每个应用都在
自己的容器中解析每个 `Binding` 的一份副本，而请求会选用其所属应用的那份副本，因此两个使用不同容器的应用
能够同时服务同一个函数。

## <a id="a-framework-without-depends"></a>不带 `Depends` 的框架

Litestar 按名称提供依赖项，aiogram 则从中间件按名称传递依赖。在这些框架中 `bind()` 不适用：用 `Framework`
提供消息，用 `client_of()` 找出处理函数的客户端参数，为每个客户端创建一个 `Binding`，由框架以自己的方式
调用它的 `get`，并在应用的 lifespan 中使用 `running()`。`nuke_di.litestar` 就是完整的示例：它以参数名
注册 `Provide(binding.get)`。

## <a id="checking-an-integration"></a>检查集成

`nuke_di.integration.testing.check()` 针对你的集成，运行每个集成都要遵守的契约：

- 处理函数通过类型提示接收客户端，该客户端在应用运行期间保持连接；
- 处理函数的依赖通过类型提示接收客户端（仅限 `DependsFramework`）；
- 启动前的 `override()` 会替换处理函数经由另一个客户端获得的客户端；
- 不经过应用的 lifespan 就调用的处理函数会抛出框架的 `not_connected` 错误；
- `connect()` 失败会以 `RuntimeError` 使应用启动失败，并让容器处于已 `flush()` 的状态。

它自带客户端和处理函数。你提供框架和两个函数：`make_app(container, handler)` 返回一个用 `container`
设置好、服务 `handler` 的新应用，`handler` 是一个除客户端外没有其他参数的 `async def`；
`run(app, lifespan)` 是一个运行应用的异步上下文管理器，仅当 `lifespan` 为真时才运行应用的 lifespan，
并 yield 一个 `send()`：它通过框架调用一次处理函数，并抛出处理函数抛出的异常。对于上面的模块：

```python
# tests/test_contract.py
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from faststream import FastStream, TestApp
from faststream.nats import NatsBroker, TestNatsBroker

from myapp.faststream_di import FRAMEWORK, setup
from nuke_di import Dependencies
from nuke_di.integration.testing import Send, check


def make_app(container: Dependencies, handler: Callable[..., Any]) -> FastStream:
    broker = NatsBroker()
    app = FastStream(broker)
    setup(app, container)
    broker.subscriber("check")(handler)
    return app


@asynccontextmanager
async def run(app: FastStream, lifespan: bool) -> AsyncIterator[Send]:
    # connect_only: FastStream would guess it from the mention of TestApp below
    async with TestNatsBroker(app.broker, connect_only=lifespan) as broker:
        if lifespan:
            async with TestApp(app):
                yield lambda: broker.publish(None, "check")
        else:
            yield lambda: broker.publish(None, "check")


async def test_contract() -> None:
    await check(FRAMEWORK, make_app, run)
```

```console
$ pytest -q tests/test_contract.py
.                                                                        [100%]
1 passed in 0.29s
```

`check()` 是一个协程：在 pytest-asyncio 或 anyio 下运行它。它抛出一个 `ExceptionGroup`，包含每个失败的
用例，每个异常都附有一条注明用例名称的 note。如果去掉 `setup()` 中 `wrap_lifespan()` 那一行，容器就永远
不会连接：

```console
$ pytest -q --tb=short tests/test_contract.py
F                                                                        [100%]
  | ExceptionGroup: the FastStream integration breaks the nuke-di contract (4 sub-exceptions)
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case handler: a handler takes a client by type hint, connected for the time the app runs
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case dependency: a dependency of a handler takes a client by type hint
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case override: override() before startup replaces a client the handler gets through another client
    | AssertionError: expected a RuntimeError with 'nuke-di clients failed to start' about Broken, got RuntimeError('Broken is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`')
    | FastStream integration, case failed_connect: a failed connect() fails the app's startup with a RuntimeError and leaves the container flushed
FAILED tests/test_contract.py::test_contract - ExceptionGroup: the FastStream...
1 failed in 0.17s
```

（traceback 已缩短。）如果框架报告处理函数的错误而不是抛出它，就需要一个会抛出该错误的 `send()`：
HTTP 框架的测试客户端返回 500，因此 `send()` 要检查状态码。Litestar 只有在 `debug=True` 时才会把错误
放进响应中。

`nuke_di._integration` 是 1.14.0 之前这套工具包所在的私有模块，现在仍可导入，但会发出
`DeprecationWarning`，并将在 1.15.0 中移除。
