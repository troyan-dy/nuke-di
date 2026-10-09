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
