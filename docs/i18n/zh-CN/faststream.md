# <a id="faststream"></a>FastStream

[English](../../guide/faststream.md) · [Русский](../ru/faststream.md) · **简体中文** · [Español](../es/faststream.md) · [Português (Brasil)](../pt-BR/faststream.md) · [日本語](../ja/faststream.md) · [Polski](../pl/faststream.md)

← [文档](../README.zh-CN.md#documentation)

FastStream 的订阅者在消息旁边通过类型提示接收客户端：

```bash
pip install "nuke-di[faststream]"
```

需要 FastStream 0.6 或更高版本，支持任意 broker。使用 [FastAPI](fastapi.md) 示例中的客户端：

```python
# app/worker.py
from faststream import FastStream
from faststream.nats import NatsBroker

from app.clients import UserService
from nuke_di.faststream import setup

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
setup(app)  # clients connect before the broker starts, disconnect after it stops


@broker.subscriber("greetings")
async def greet(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))
```

```console
$ faststream run app.worker:app
database: connected
2026-10-08 15:12:52,281 INFO     - FastStream app starting...
2026-10-08 15:12:52,287 INFO     - greetings |            - `Greet` waiting for messages
2026-10-08 15:12:52,287 INFO     - FastStream app started successfully! To exit, press CTRL+C
2026-10-08 15:12:55,078 INFO     - greetings | a747e4d0-2 - Received
Hello, user-42!
2026-10-08 15:12:55,079 INFO     - greetings | a747e4d0-2 - Processed
^C
2026-10-08 15:12:56,222 INFO     - FastStream app shutting down...
2026-10-08 15:12:56,223 INFO     - FastStream app shut down gracefully.
database: disconnected
```

消息是这样发布的：

```python
# publish.py
import asyncio

from faststream.nats import NatsBroker


async def main() -> None:
    async with NatsBroker("nats://localhost:4222") as broker:
        await broker.publish(42, "greetings")


asyncio.run(main())
```

规则如下：

- **在哪里填充客户端。** 在应用的 broker 的订阅者（也包括所包含路由器中的订阅者）的参数中，
  以及它们用到的每个 `Depends(...)` 的参数中，不论嵌套多深：既包括函数，也包括类，还包括
  订阅者、其路由器和 broker 上的 `dependencies=`。其余参数都交给 FastStream 处理：消息、
  消息的字段、`Context()`。
- **哪些客户端会启动。** 启动时，应用的 broker 所服务的每个订阅者（包括路由器中的订阅者）
  的客户端都会启动。订阅者可以在 `setup(app)` 之前或之后声明。
- **lifespan。** 客户端在应用自己的 `lifespan=` 和 `on_startup=` 钩子之前、在 broker 启动之前连接；
  在 broker 停止之后、在 `after_shutdown=` 钩子之后断开。`Shutdown` 和 `BackgroundTasks`
  的行为与 [FastAPI](fastapi.md) 中相同。`setup()` 也适用于 `AsgiFastStream`。
- **实例。** 与 `inject()` 一样，`Client` 在每个容器中只有一个实例，而
  `NotSingletonClient` 是每个声明它的参数一个实例，而不是每条消息一个。
- **函数仍然是函数。** FastStream 看到的签名是 `Annotated[UserService, Depends(...)]`，
  与 [FastAPI](fastapi.md) 中相同。
- **一次一个应用。** 订阅者函数及其依赖只会被重写一次，与容器无关，因此共享它们的应用
  （例如在模块级 broker 上每个测试一个应用）要依次运行：当另一个使用同一函数的应用正在运行时，
  新启动的应用会启动失败。接收客户端的依赖函数只能服务于 FastAPI 或 FastStream 的处理函数，不能同时服务两者。

**测试。** FastStream 的测试 broker 不运行任何应用钩子，因此要在其中用 `TestApp` 启动应用：

```python
# tests/test_worker.py
import pytest
from faststream import TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.worker import app, broker
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_greet(capsys: pytest.CaptureFixture[str]) -> None:
    with DI.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(1, "greetings")

    assert "Hello, alice!" in capsys.readouterr().out
```

```console
$ pytest -q tests/test_worker.py
.                                                                        [100%]
1 passed in 0.14s
```

没有经过应用的 lifespan 就处理的消息，例如通过不带 `TestApp` 的 `TestNatsBroker(broker)`
处理的消息，会抛出 `RuntimeError: UserService is not connected: start the app with its lifespan`。
在应用启动之后才添加的订阅者会抛出 `RuntimeError: UserService was not started with the app`。

## <a id="publishing-from-a-client"></a>从客户端发布消息

负责发布消息的客户端（例如 outbox 或通知器）在 `__init__` 中接收应用的 broker，并把 broker 的
生命周期交给 FastStream 管理：

```python
# app/notify.py
from faststream import FastStream
from faststream.nats import NatsBroker

from app.clients import UserService
from nuke_di import Client
from nuke_di.faststream import setup

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
setup(app)


class Notifications(Client):
    # The app's broker: FastStream starts it after the clients connect and stops it before they
    # disconnect, so connect() and disconnect() leave it alone
    def __init__(self, nats: NatsBroker = broker) -> None:
        self._nats = nats

    async def send(self, text: str) -> None:
        await self._nats.publish(text, "notifications")


@broker.subscriber("greetings")
async def greet(user_id: int, users: UserService, notifications: Notifications) -> None:
    await notifications.send(await users.greet(user_id))


@broker.subscriber("notifications")
async def show(text: str) -> None:
    print(f"notification: {text}")
```

```console
$ faststream run app.notify:app
database: connected
2026-10-10 18:39:08,837 INFO     - FastStream app starting...
2026-10-10 18:39:08,842 INFO     - greetings     |            - `Greet` waiting for messages
2026-10-10 18:39:08,843 INFO     - notifications |            - `Show` waiting for messages
2026-10-10 18:39:08,843 INFO     - FastStream app started successfully! To exit, press CTRL+C
2026-10-10 18:39:11,811 INFO     - greetings     | 7c3cf44a-9 - Received
2026-10-10 18:39:11,811 INFO     - greetings     | 7c3cf44a-9 - Processed
2026-10-10 18:39:11,812 INFO     - notifications | 5ddab782-7 - Received
notification: Hello, user-42!
2026-10-10 18:39:11,812 INFO     - notifications | 5ddab782-7 - Processed
^C
2026-10-10 18:39:12,908 INFO     - FastStream app shutting down...
2026-10-10 18:39:12,909 INFO     - FastStream app shut down gracefully.
database: disconnected
```

消息是用上文的 `publish.py` 发布的。在测试中，测试 broker 会像路由其他消息一样路由客户端发布的消息，
也可以直接替换客户端：

```python
# tests/test_notify.py
from faststream import TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.notify import Notifications, app, broker, show
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


class FakeNotifications(Notifications):
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


async def test_greet_publishes() -> None:
    with DI.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(1, "greetings")

            show.mock.assert_called_once_with("Hello, alice!")


async def test_greet_with_fake_notifications() -> None:
    fake = FakeNotifications()
    with DI.override(Notifications, fake):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(42, "greetings")

    assert fake.sent == ["Hello, user-42!"]
```

```console
$ pytest -q tests/test_notify.py
..                                                                       [100%]
2 passed in 0.19s
```

规则如下：

- **broker 属于应用。** FastStream 在客户端连接之后启动它，在客户端断开之前停止它，因此这个客户端
  不像第三方对象的客户端通常那样在 `connect()` 中创建自己的 broker
  （[ADR-0005](../../adr/0005-third-party-objects-as-client-classes.md)）：broker 归 FastStream 所有。
  客户端在应用运行期间从自己的方法中发布消息。
- **不要在 `connect()` 或 `disconnect()` 中发布。** 在真实的 broker 上，`connect()` 中的 `publish()`
  会抛出 `faststream.exceptions.IncorrectState`，因为 broker 尚未启动，应用会启动失败，报错
  `RuntimeError: nuke-di clients failed to start: Notifications.connect() raised IncorrectState`。在
  `disconnect()` 中它会抛出同样的异常，因为 broker 已经停止，但 nuke-di 只会记录失败的 `disconnect()`，
  应用照常退出。在 `TestNatsBroker` 下，前者抛出
  ``SetupError: You should setup `HandlerItem` at first.``，后者则静默通过，因此测试发现不了
  `disconnect()` 中的发布。
- **broker 是参数的默认值**，而不是客户端：nuke-di 只填充类型为客户端的参数，其余参数保留默认值。
  单元测试可以直接构建 `Notifications(nats=AsyncMock())`。
- **在 `TestNatsBroker` 下**，被打补丁的是同一个 broker 对象，因此客户端在内存中发布消息，
  `show.mock` 能看到这条消息；`override(Notifications, ...)` 会为所有接收该客户端的订阅者替换它。
- **没有 FastStream 应用的进程**（例如发送消息的 `@job`）自己持有连接：此时 broker 在客户端的
  `connect()` 中创建，在它的 `disconnect()` 中停止，就像
  [examples/faststream_nats/publish.py](../../../examples/faststream_nats/publish.py) 中的 `Nats`。
- **多个 broker**，即 `FastStream(first, second)`，用法相同：`setup(app)` 会填充每个 broker 的订阅者，
  客户端则把它要发布到的 broker 作为默认值。

## <a id="one-app-at-a-time-in-tests"></a>测试中一次一个应用

FastStream 每次启动都会重新构建订阅者，因此 nuke-di 只重写订阅者函数一次，与容器无关
（即[编写集成](integrations.md)中的 `per_container=False`），每个启动的应用都从自己的容器中填充它。
因此，依次启动应用的测试可以在模块级 broker 上为每个应用分配各自的容器：

```python
# tests/test_containers.py
from faststream import FastStream, TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.notify import broker, show
from nuke_di import Dependencies
from nuke_di.faststream import setup


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def make_app(container: Dependencies) -> FastStream:
    # A new app on the module-level broker, whose subscribers are declared on import
    app = FastStream(broker)
    setup(app, container)
    return app


async def test_greet(di: Dependencies) -> None:
    with di.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(make_app(di)):
            await test_broker.publish(1, "greetings")

            show.mock.assert_called_once_with("Hello, alice!")
```

```console
$ pytest -q tests/test_containers.py
.                                                                        [100%]
1 passed in 0.15s
```

应对方式：

- **依次运行会启动应用的测试**，pytest 默认就是这样做的。pytest-xdist 会在各自独立、互不共享的进程中
  运行它们。
- **不要同时在相同的订阅者函数上启动两个应用**，例如在一个 `TestApp` 里再嵌套另一个。第二个应用会
  启动失败，报错 `RuntimeError: nuke-di clients
  failed to start: UserService is filled for another app that is running; apps that share a handler
  function run one at a time`，而不是把第一个应用的客户端交给第二个应用。
- **在模块级应用上使用 `DI.override()`，或每个测试一个容器**，两种方式都可以；按其余测试的用法来选择。
- 与此不同，FastAPI 的请求拿到的是它所到达的那个应用的客户端，因此不同容器上的 FastAPI 应用可以同时
  服务同样的函数，参见 [FastAPI](fastapi.md#an-app-per-test-container)。
