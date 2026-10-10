# <a id="taskiq"></a>taskiq

[English](../../guide/taskiq.md) · [Русский](../ru/taskiq.md) · **简体中文** · [Español](../es/taskiq.md) · [Português (Brasil)](../pt-BR/taskiq.md) · [日本語](../ja/taskiq.md) · [Polski](../pl/taskiq.md)

← [文档](../README.zh-CN.md#documentation)

taskiq 的任务在它被投递时所带的参数旁边，通过类型提示接收客户端：

```bash
pip install "nuke-di[taskiq]"
```

需要 taskiq 0.11 或更高版本，支持任意 broker。使用 [FastAPI](fastapi.md) 示例中的客户端，以及
`InMemoryBroker`——它在投递任务的同一个进程中运行任务：

```python
# app/tasks.py
from typing import Annotated

from taskiq import Context, InMemoryBroker, TaskiqDepends

from app.clients import Database, UserService
from nuke_di.taskiq import setup

broker = InMemoryBroker()  # runs the tasks in this process
setup(broker)  # clients connect when the worker starts, disconnect when it stops


@broker.task
async def send_report(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))


async def account_name(context: Annotated[Context, TaskiqDepends()], db: Database) -> str:
    # A dependency gets taskiq's own objects and clients side by side
    return await db.fetch_user(context.message.kwargs["account_id"])


@broker.task
async def close_account(account_id: int, name: Annotated[str, TaskiqDepends(account_name)]) -> str:
    return f"closed the account of {name}"
```

```python
# app/main.py
import asyncio

from app.tasks import broker, close_account, send_report


async def main() -> None:
    # An InMemoryBroker is its own worker: its startup connects the clients
    await broker.startup()
    report = await send_report.kiq(42)
    await report.wait_result()
    closed = await close_account.kiq(account_id=7)
    print((await closed.wait_result()).return_value)
    await broker.shutdown()


asyncio.run(main())
```

```console
$ python -m app.main
database: connected
Hello, user-42!
closed the account of user-7
database: disconnected
```

**类型检查器。** taskiq 用任务自身的签名为 `.kiq()` 标注类型，因此 mypy 和 pyright 会要求
`send_report.kiq(42)` 传入 `users`；凡是由 taskiq 填充的参数都是如此，不论是否使用 `Annotated`。
给参数一个默认值，类型检查器就会把它视为可选参数，而客户端仍然按类型从容器中获得：

```python
@broker.task
async def send_report(user_id: int, users: UserService = TaskiqDepends()) -> None:
    print(await users.greet(user_id))
```

Ruff 的 `B008` 会标记默认值中的函数调用；taskiq 的标记放在那里是安全的：

```toml
# pyproject.toml
[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["taskiq.TaskiqDepends"]
```

**真正的 worker。** 在部署中，broker 是某个队列的 broker，例如
[taskiq-nats](https://github.com/taskiq-python/taskiq-nats) 中的 `NatsBroker`；其余一切不变：

```python
# app/tasks.py
import os

from taskiq_nats import NatsBroker

from app.clients import UserService
from nuke_di.taskiq import setup

broker = NatsBroker(os.environ.get("NATS_URL", "nats://localhost:4222"), queue="reports")
setup(broker)


@broker.task
async def send_report(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))
```

worker 进程负责连接客户端；只投递任务的进程（例如 Web 应用）只连接 broker，别的什么都不连接：

```python
# app/kick.py
import asyncio

from app.tasks import broker, send_report


async def main() -> None:
    # A client process: the broker connects to NATS, the clients stay unconnected
    await broker.startup()
    await send_report.kiq(42)
    print("kicked send_report(42)")
    await broker.shutdown()


asyncio.run(main())
```

```console
$ taskiq worker app.tasks:broker --workers 1
[2026-10-10 18:40:00,853][taskiq.worker][INFO   ][MainProcess] Pid of a main process: 56250
[2026-10-10 18:40:00,853][taskiq.worker][INFO   ][MainProcess] Starting 1 worker processes.
[2026-10-10 18:40:00,859][taskiq.process-manager][INFO   ][MainProcess] Started process worker-0 with pid 56252
database: connected
[2026-10-10 18:40:01,084][nuke_di.core][INFO   ][worker-0] Connected 2 clients in 0.00s (slowest: Database 0.00s, UserService 0.00s)
[2026-10-10 18:40:01,091][taskiq.receiver.receiver][INFO   ][worker-0] Listening started.
[2026-10-10 18:40:03,815][taskiq.receiver.receiver][INFO   ][worker-0] Executing task app.tasks:send_report with ID: e987ddad37444fa5a0ade0174578c9f2
Hello, user-42!
^C
[2026-10-10 18:40:05,845][taskiq.process-manager][INFO   ][MainProcess] Workers are scheduled for shutdown.
[2026-10-10 18:40:05,995][taskiq.process-manager][INFO   ][MainProcess] Stopped process worker-0 with pid 56252
[2026-10-10 18:42:01,140][taskiq.receiver.receiver][INFO   ][worker-0] Stopping prefetching messages...
[2026-10-10 18:42:01,143][taskiq.receiver.receiver][INFO   ][worker-0] The runner is stopped.
[2026-10-10 18:42:01,144][taskiq.worker][INFO   ][worker-0] Shutting down the broker.
database: disconnected
```

在另一个终端中：

```console
$ python -m app.kick
kicked send_report(42)
```

`Stopping prefetching messages...` 之前的两分钟来自 taskiq：它的 worker 进程（taskiq 0.13 搭配
taskiq-nats 0.7）要等到下一条消息或下一次 NATS ping 才会注意到信号。

规则如下：

- **在哪里填充客户端。** 在 broker 的任务的参数中，以及它们用到的每个 `TaskiqDepends(...)`
  函数的参数中，不论嵌套多深，也包括生成器依赖。其余参数都交给 taskiq 处理：`.kiq()` 的参数、
  `Context`、`TaskiqState`。依赖类 `Annotated[Auth, TaskiqDepends()]` 由 taskiq 根据它自己的
  `__init__` 构建，而 nuke-di 不会重写这个 `__init__`：需要客户端的类本身就应当是一个客户端，
  或者通过依赖函数获取客户端。
- **哪些客户端会启动。** `broker.get_all_tasks()` 中每个任务的客户端：既包括 broker 自己的任务，
  也包括共享任务（`@shared_task`）。任务可以在 `setup(broker)` 之前或之后声明，共享任务则只能在它之前声明：
  共享任务注册在 taskiq 的共享 broker 上，而 `setup()` 不会挂钩那个 broker。
- **由哪个进程连接。** 由 broker 触发 `WORKER_STARTUP` 的那个进程：`taskiq worker` 进程，以及任何启动
  `InMemoryBroker` 的进程——它自己就是自己的 worker。只投递任务的进程以 `CLIENT_STARTUP` 启动 broker，
  不连接任何客户端。因此同一个包含 broker 的模块可以同时服务两边：worker 通过 taskiq 连接，
  Web 应用通过它自己的集成连接。
- **lifespan。** 客户端在其他 `WORKER_STARTUP` 处理函数（包括在 `setup()` 之前注册的处理函数）之前连接，
  并在 `broker.shutdown()` 运行完 `WORKER_SHUTDOWN` 处理函数、中间件和结果后端之后断开。
  `connect()` 失败会让 `broker.startup()` 失败，worker 也随之失败。`Shutdown` 和 `BackgroundTasks`
  的行为与 [FastAPI](fastapi.md) 中相同。
- **实例。** 与 `inject()` 一样，`Client` 在每个容器中只有一个实例，而 `NotSingletonClient`
  是每个声明它的参数一个实例，而不是每个任务一个。
- **函数仍然是函数。** taskiq 看到的签名是 `Annotated[UserService, TaskiqDepends(...)]`，
  与 [FastAPI](fastapi.md) 中相同；`await send_report(1, users)` 会用手动传入的客户端调用它。
- **一个容器只连接一次。** 在容器已经连接的进程中启动的 `InMemoryBroker`，例如在使用同一个 `DI`
  的 FastAPI 应用内部启动，会以 `RuntimeError: nuke-di clients failed to start: the container is
  already connected` 失败。请给这样的 broker 一个独立的容器：`setup(broker, container=Dependencies())`。
  任务函数一次只服务一个 broker，与 FastStream 的订阅者一样。

**测试。** 在 `InMemoryBroker` 上运行任务，并在 override 内部启动它：

```python
# tests/test_tasks.py
import pytest

from app.clients import Database
from app.tasks import broker, send_report
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_send_report(capsys: pytest.CaptureFixture[str]) -> None:
    with DI.override(Database, FakeDatabase()):
        await broker.startup()
        task = await send_report.kiq(1)
        await task.wait_result()
        await broker.shutdown()

    assert "Hello, alice!" in capsys.readouterr().out
```

```console
$ pytest -q tests/test_tasks.py
.                                                                        [100%]
1 passed in 0.38s
```

没有经过 worker 启动就运行的任务，例如投递到一个从未启动的 `InMemoryBroker` 的任务，
会在结果中带着 ``RuntimeError: UserService is not connected: the clients connect when the worker starts;
run tasks with `taskiq worker`, or start an InMemoryBroker with `await broker.startup()` before kicking them``
失败。在 worker 启动之后才声明的任务会以 `RuntimeError: UserService was not started with the worker` 失败。
