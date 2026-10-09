# <a id="testing"></a>测试

[English](../../guide/testing.md) · [Русский](../ru/testing.md) · **简体中文** · [Español](../es/testing.md) · [Português (Brasil)](../pt-BR/testing.md) · [日本語](../ja/testing.md) · [Polski](../pl/testing.md)

← [文档](../README.zh-CN.md#documentation)

**通过容器测试客户端。** 在解析依赖树之前注册 mock；之后每个使用方
都会收到这个 mock：

```python
from unittest.mock import call

from nuke_di import Dependencies


async def test_greet() -> None:
    deps = Dependencies()
    db = deps.mock(Database)
    db.fetch_user.return_value = "alice"

    users = deps.resolve(UserService)
    async with deps:
        assert await users.greet(1) == "Hello, alice!"

    assert db.fetch_user.await_args_list == [call(1)]
```

**用 `override()` 在一个代码块内替换客户端。** `override(cls, new=None)` 与 `mock()` 一样注册一个替换对象，
但它一直有效到 `with` 块结束，即使跨越多次 `async with`
周期也是如此；退出时容器会被清空，因此借助它解析出的任何东西都不会泄漏到下一个测试中。
它同样适用于全局 `DI`：

```python
# test_greet.py, with Database, UserService and handler from the Quick start
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_greet_with_fake() -> None:
    with DI.override(Database, FakeDatabase()):
        injected = DI.inject(handler)
        async with DI:
            print(await injected(1))

    print("after the block:", DI.clients)


async def test_greet_with_autospec() -> None:
    with DI.override(Database) as db:  # an autospec mock by default
        db.fetch_user.return_value = "bob"
        injected = DI.inject(handler)
        async with DI:
            print(await injected(2))

    db.fetch_user.assert_awaited_once_with(2)
```

本节中的异步测试使用 [pytest-asyncio](https://pypi.org/project/pytest-asyncio/)，并在
`pytest.ini` 中设置 `asyncio_mode = auto`；否则 pytest 不会运行 `async def` 测试。

```console
$ pytest -q -s test_greet.py
Hello, alice!
after the block: OrderedDict()
.Hello, bob!
.
2 passed in 0.01s
```

`database: connected` 从未打印：替换对象不会被连接。

规则如下：

- **先替换，再解析。** 如果在 `cls` 已被解析之后才注册替换对象，它只会影响
  之后解析的使用方，而之前的使用方仍持有真实的客户端，因此 `mock()`
  会直接抛出异常：

  ```python
  DI.inject(handler)  # resolves UserService -> Database
  DI.mock(Database)  # ConnectError: Database is already resolved, call mock() before resolve() or inject()
  ```

- **`override()` 要求容器中没有已解析的客户端。** 否则退出时的清空
  会悄悄丢弃代码块之前解析的内容，因此它会抛出
  `ConnectError: override(Database) needs a container without resolved clients, found: Database, UserService`。
  请先调用 `DI.flush()`，或使用下文的 `global_di` fixture。
- **每个类只能有一个替换对象。** 再次调用 `mock(cls)` 会返回已注册的替换对象；
  `mock(cls, other)` 和 `override(cls)` 会抛出 `ConnectError: Database already has a replacement`。
- **替换对象不会被连接。** 它们的 `connect()` / `disconnect()` 永远不会被调用，
  也不参与[按层连接](clients.md#layers)。
- **替换对象的有效期。** 由 `mock()` 注册的替换对象会在下一次 `flush()` 时被丢弃，包括
  `disconnect()` 末尾的那一次：需要多次连接容器的测试应使用
  `override()`，它的替换对象会在每次 `flush()` 后保留下来，直到代码块结束。代码块内的异常
  会原样传播；如果在容器仍处于连接状态时正常离开代码块，
  则会抛出 `ConnectError`。
- **嵌套。** 针对不同类的代码块可以嵌套，只要每个代码块都在解析任何东西之前打开即可，
  例如 `with DI.override(Database), DI.override(Clock):`；离开内层代码块时，
  外层的替换对象仍然保留。

**pytest fixture。** 安装 `nuke-di` 会注册一个提供两个 fixture 的 pytest 插件。它们都不是
autouse 的，因此现有测试的运行方式完全不变：

| Fixture     | 提供的内容                                             |
|-------------|--------------------------------------------------------|
| `di`        | 为单个测试准备的全新 `Dependencies`                    |
| `global_di` | 全局 `DI`，在测试前后都会被清空                        |

让容器保持连接状态的测试会在 teardown 时报错，而容器仍会
被清空，因此下一个测试能从干净的状态开始：

```python
# test_users.py, with Database, UserService and handler from the Quick start
from nuke_di import Dependencies


async def test_greet(di: Dependencies) -> None:
    di.mock(Database).fetch_user.return_value = "alice"
    users = di.resolve(UserService)
    async with di:
        assert await users.greet(1) == "Hello, alice!"


async def test_handler(global_di: Dependencies) -> None:  # e.g. code that calls DI.inject()
    global_di.mock(Database).fetch_user.return_value = "bob"
    injected = global_di.inject(handler)
    async with global_di:
        assert await injected(2) == "Hello, bob!"


async def test_forgets_to_disconnect(di: Dependencies) -> None:
    di.resolve(UserService)
    await di.connect()
```

```console
$ pytest -q test_users.py
...E                                                                     [100%]
==================================== ERRORS ====================================
_______________ ERROR at teardown of test_forgets_to_disconnect ________________
the test left the container of the "di" fixture connected; its clients were not disconnected, use `async with` or call disconnect()
----------------------------- Captured stdout call -----------------------------
database: connected
=========================== short test summary info ============================
ERROR test_users.py::test_forgets_to_disconnect - Failed: the test left the c...
3 passed, 1 error in 0.01s
```

fixture 无法自行断开被遗忘的容器：到 teardown 时，测试的事件循环
可能已经关闭。`global_di` 只保护请求了它的测试：不经过它而直接使用全局
`DI` 的测试，仍可能给下一个测试留下客户端。自行定义了
`di` fixture 的项目会继续使用自己的版本，因为 `conftest.py` 中的 fixture 优先于插件中的 fixture；
`pytest -p no:nuke_di` 可以关闭该插件。

**直接测试 job。** 导入模块不会运行 job，因此可以直接用
mock 和参数调用该函数：

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    pg.upsert.assert_awaited_once_with("users", ["row"])
```

**通过容器测试 job**，客户端的装配方式与生产环境相同：

```python
async def test_sync_with_container() -> None:
    deps = Dependencies()
    pg = deps.mock(Postgres)  # mocks first: resolve() and inject() reuse them
    warehouse = deps.mock(Warehouse)
    warehouse.changes.return_value = ["row"]
    injected = deps.inject(sync)

    async with deps:
        await injected(day=datetime.date(2026, 10, 1), tables=["users"])

    assert pg.upsert.await_args_list == [call("users", ["row"])]
```

**每个入口点都能解析。** 导入模块不会运行其中的 job 或 worker，而 `inject()` 构建依赖树时不连接任何东西，
所以一个测试就能在 CI 中检查所有入口点的接线：循环依赖、没有类型注解的参数、不是客户端的必填参数或抛出异常的
`__init__` 都会让它失败，错误与真实运行打印的一样，并且不需要数据库：

```python
# test_wiring.py
from collections.abc import Callable

import pytest

from nuke_di import Dependencies

from app.jobs import sync
from app.workers import consumer


@pytest.mark.parametrize("entrypoint", [sync.sync, consumer.consumer])
def test_entrypoint_resolves(entrypoint: Callable[..., object]) -> None:
    Dependencies().inject(entrypoint)  # runs every __init__, connects nothing
```

```console
$ pytest -q test_wiring.py
..                                                                       [100%]
2 passed in 0.05s
```

保留容器，就能得到该入口点的[依赖图](clients.md#the-graph)，放进它的 README：
`deps = Dependencies(); deps.inject(sync.sync); print(deps.graph().to_mermaid())`。

**测试 worker。** `Shutdown.set()` 的作用与 SIGTERM 相同：

```python
async def test_consumer_stops_on_shutdown() -> None:
    queue, shutdown = AsyncMock(), Shutdown()

    async def last_message() -> str:
        shutdown.set()  # what SIGTERM would do
        return "message-1"

    queue.get.side_effect = last_message

    await consumer(queue, shutdown)

    queue.get.assert_awaited_once()
```
