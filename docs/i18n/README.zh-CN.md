# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](zh-CN/development.md)
[![License](https://img.shields.io/pypi/l/nuke-di)](../../LICENSE)

[English](https://github.com/troyan-dy/nuke-di/blob/master/README.md) · [Русский](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ru.md) · **简体中文** · [Español](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.es.md) · [Português (Brasil)](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pt-BR.md) · [日本語](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ja.md) · [Polski](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pl.md)

为异步 Python 项目打造的最简单的依赖注入。

依赖用普通的类型提示声明即可。`nuke-di` 会构建依赖树，每个客户端只创建一次，并驱动它的异步生命周期：
启动时调用 `connect()`，关闭时调用 `disconnect()`。每个客户端在自己的依赖连接完成后立即启动，
与其他所有已就绪的客户端并发进行。

在此之上，只需一个装饰器就能把异步函数变成一个带命令行参数的进程；
FastAPI、Litestar 和 FastStream 的处理函数也以同样的方式通过类型提示接收客户端。

它提取自一个生产环境 Python 微服务框架的 DI 层，没有任何运行时依赖。

- [安装](#installation) · [快速开始](#quick-start) · [原则](#principles) · [性能](#performance)
- 示例：[带命令行参数的 job](#a-job-with-command-line-arguments) · [FastAPI](#fastapi)
- [文档](#documentation)

## <a id="installation"></a>安装

```bash
pip install nuke-di
```

需要 Python 3.11+。

## <a id="quick-start"></a>快速开始

```python
import asyncio

from nuke_di import DI, Client


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


async def handler(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def main() -> None:
    injected = DI.inject(handler)  # resolves UserService -> Database

    async with DI:  # connect() every client, disconnect() on exit
        print(await injected(42))


asyncio.run(main())
```

```text
database: connected
Hello, user-42!
database: disconnected
```

执行过程：

1. `DI.inject(handler)` 读取 `handler` 的类型提示，找到客户端 `UserService`，发现它的 `__init__`
   需要一个 `Database`，于是把两者都构建出来。`user_id: int` 不是客户端，因此仍是普通参数。
2. `async with DI` 对它构建的每个客户端调用 `connect()`，依赖先连接。
3. `injected(42)` 调用了 `handler(42, users=<UserService>)`。
4. 离开 `async with` 块时，按相反顺序调用 `disconnect()`。

## <a id="principles"></a>原则

- **依赖就是一个类。** 一个带类型提示的 `__init__` 以及异步 `connect()` / `disconnect()` 的 `Client` 子类
  就是全部模型：没有 provider，没有模块，没有注册，也没有需要配置的作用域。第三方对象只要包装进这样一个类，
  就成了依赖。
- **类型提示就是装配。** 客户端在 `__init__` 中请求自己的依赖，函数则在自己的签名中请求。除此之外没有任何地方
  指名它们，因此重命名或新增一个依赖只是一次普通的重构。
- **并发启动，有序关闭。** 客户端在自己的依赖连接完成后立即连接，与其他所有已就绪的客户端并发进行，
  因此一个慢的客户端只会拖住需要它的那些客户端。断开时顺序相反，某个 `disconnect()` 失败也不会妨碍其他客户端断开。
- **快速失败。** 无法构建的依赖树会在任何连接发生之前失败，并指出有问题的参数以及通往它的路径；
  启用[插件](zh-CN/clients.md#checking-the-tree-with-mypy)后，`mypy` 会在进程启动之前报告同样的错误。
  无法连接的客户端会在已连接的客户端断开之后让应用停止。没有重试：重启是编排系统的职责。
- **测试只做替换，不改装配。** `mock()` 和 `override()` 在单个测试中用替身代替客户端；
  被测代码无需改动。
- **没有运行时依赖。** 核心只使用标准库；各框架集成作为 extras 提供。

启动过程是这样的，以[客户端指南](zh-CN/clients.md#connect-order)中的示例为例：`Consumer` 只需要 `Kafka`，
所以它不会等待较慢的 `Postgres`，整个启动耗时等于最长的那条依赖链。

![六个客户端按各自的依赖连接：Kafka 和 Redis 连接完成后 Consumer 和 Http 立即启动，启动耗时 0.35s](https://raw.githubusercontent.com/troyan-dy/nuke-di/66f74f76407da320cfc97ef22b761d85e298eddd/docs/connect-now.svg)

## <a id="performance"></a>性能

面对真实的连接，启动的代价在于等待，而决定启动时间的是依赖树的结构。以商品页后端为例：一个 API、四个功能模块，
每个功能模块有四个耗时 100–300 ms 的连接，共 21 个客户端：

![由 21 个客户端组成的商品页：连接、功能模块和 API。nuke-di 和 dependency-injector 用 0.34 s 启动，dishka 和 wireup 用 3.41 s](https://raw.githubusercontent.com/troyan-dy/nuke-di/c402c5086426dc28c0886f62656fcf9d901c5de8/docs/product-page.svg)

`nuke-di` 在每个客户端自身的依赖连接完成后立即连接它，所以启动时间等于最长的依赖链，0.34 s。dishka 和 wireup
在一次 `get()` 中逐个连接客户端：3.41 s，是前者的十倍，而且依赖树越宽，差距越大。如果手动并发获取四个功能模块，wireup 可以降到 0.95 s；
dishka 也可以，但必须关闭它的锁，而这样一来，两个功能模块共享的客户端会被创建两次。dependency-injector 在每个客户端都手写成 `Resource` 时启动同样快，但关闭是分层进行的，
每一层都要等待最慢的客户端：0.66 s 对比 0.37 s，因为关闭各需 300 ms 的 `Checkout` 和 `EventsProducer` 位于不同的层。
injector 没有异步生命周期。

```console
$ uv run python benchmarks/compare.py --only connect --summary
nuke-di 1.14.3 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 17c8815 · N = 10, 100, 1000 · 20 repeats
nuke-di 1.14.3 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

| Lower is better                          | nuke-di     | dishka         | wireup         | dependency-injector | injector |
|------------------------------------------|------------:|---------------:|---------------:|--------------------:|---------:|
| Startup: 8 clients, connect() of 1–60 ms | **70.8 ms** | 156 ms (2.2×)  | 157 ms (2.2×)  | **71.5 ms**         | —        |
| Shutdown: the same 8 clients             | **18.8 ms** | 30.9 ms (1.6×) | 30.5 ms (1.6×) | 26.0 ms (1.4×)      | —        |
| Startup: the product page, 21 clients    | **335 ms**  | 3.41 s (10.2×) | 3.41 s (10.2×) | **337 ms**          | —        |
| Shutdown: the product page               | **365 ms**  | 850 ms (2.3×)  | 850 ms (2.3×)  | 657 ms (1.8×)       | —        |
```

这三个库在创建容器时都不会建立任何连接：除非应用在启动时获取根对象，否则第一个请求会等待这些连接，并随它们一起失败。
`nuke-di` 在 `async with DI` 中连接每个客户端，无法连接的客户端会让启动失败。

`benchmarks/compare.py` 把同样的客户端依赖树交给 dishka、wireup、dependency-injector 和 injector，各自按自己的方式注册同一组类：
在进程中全新的类上冷启动容器并解析根节点、再次获取根节点，以及通过各库的集成发起一次 FastAPI 请求：

```console
$ uv run python benchmarks/compare.py --size 100 --summary
nuke-di 1.11.1 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 6c2ae10 · N = 100 · 20 repeats
nuke-di 1.11.1 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

| Lower is better                                          | nuke-di        | dishka          | wireup          | dependency-injector | injector        |
|----------------------------------------------------------|---------------:|----------------:|----------------:|--------------------:|----------------:|
| Cold start: a container and a tree of 100 clients        | **541 µs**     | 12.9 ms (23.8×) | 20.0 ms (37.0×) | 1.05 ms (1.9×)      | 1.34 ms (2.5×)  |
| Cold start: the same 100 clients with string annotations | 1.27 ms (1.2×) | 13.7 ms (12.6×) | 21.4 ms (19.6×) | **1.09 ms**         | 1.47 ms (1.3×)  |
| A cached root                                            | 94.1 ns (2.5×) | 261 ns (7.1×)   | 92.6 ns (2.5×)  | **37.0 ns**         | 1.18 µs (31.9×) |
| A FastAPI request with a client                          | **103 µs**     | 107 µs (1.0×)   | 206 µs (2.0×)   | 221 µs (2.2×)       | —               |
```

![nuke-di against other DI libraries: lower is better](../benchmarks/compare.png)

那么 `nuke-di` 是最快的吗？在使用真实类型注解构建依赖树和 FastAPI 请求上，是的：dependency-injector 和 injector 构建依赖树
要多花 2–2.5 倍时间，dishka 和 wireup 因创建容器时的图校验要多花 24–37 倍，wireup 和 dependency-injector 每个请求多付出一倍。
使用字符串注解时，不读取任何注解的 dependency-injector 领先约五分之一。在缓存根节点上，`nuke-di` 与 wireup 持平，
dependency-injector 的 Cython `get()` 领先约 50 ns，这个差距任何应用都察觉不到。

单独来看，`resolve()` 每个客户端耗时 3.5–6.3 µs，因此 1000 个客户端的依赖树在 5.5 ms 内建成；`connect()` 对每个客户端
增加 13–18 µs。[docs/benchmarks.md](../benchmarks.md) 解释每个场景，记录 Python 3.11–3.14 上的基线，并给出完整的对比及其方法。

## <a id="a-job-with-command-line-arguments"></a>带命令行参数的 job

只需一个装饰器，异步函数就能成为一个进程的主程序。客户端会被注入，其他每个带注解的参数
都会成为一个命令行选项，带有类型并经过校验：

```python
# sync.py
import datetime
import enum
from typing import Annotated

from nuke_di import Client, Option, job


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")

    async def upsert(self, table: str, rows: list[str]) -> None:
        print(f"postgres: upserted {len(rows)} rows into {table}")


class Warehouse(Client):
    async def changes(self, table: str, day: datetime.date) -> list[str]:
        return [f"{table}:{day}:{n}" for n in range(3)]


class Mode(enum.Enum):
    INCREMENTAL = "incremental"
    FULL = "full"


@job
async def sync(
    pg: Postgres,
    warehouse: Warehouse,
    day: Annotated[datetime.date, Option(help="Day to copy, YYYY-MM-DD", short="d")],
    tables: Annotated[
        list[str] | None, Option(help="Table to copy, repeat for several; all by default", short="t")
    ] = None,
    mode: Mode = Mode.INCREMENTAL,
    dry_run: Annotated[bool, Option(help="Read the changes, write nothing")] = False,
) -> None:
    """Copy one day of changes from the warehouse into Postgres."""
    print(f"sync: {mode.name} copy of {day}")
    for table in tables or ["users", "orders"]:
        rows = await warehouse.changes(table, day)
        if dry_run:
            print(f"sync: would upsert {len(rows)} rows into {table}")
        else:
            await pg.upsert(table, rows)
```

没有 `main()`，没有 `asyncio.run()`，也没有 `argparse`：装饰器会解析并连接客户端、处理命令行、
运行函数，然后以含义明确的退出码退出：

```console
$ python sync.py --day 2026-10-01
postgres: connected
sync: INCREMENTAL copy of 2026-10-01
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected

$ python sync.py -d 2026-10-01 -t users --mode FULL --dry-run
postgres: connected
sync: FULL copy of 2026-10-01
sync: would upsert 3 rows into users
postgres: disconnected
```

`--help` 根据函数签名和 docstring 自动生成（Python 3.13+ 输出的是 `-d, --day DAY`，而不是 `-d DAY, --day DAY`）：

```console
$ python sync.py --help
usage: sync.py [-h] -d DAY [-t TABLES] [--mode {INCREMENTAL,FULL}]
               [--dry-run | --no-dry-run]

Copy one day of changes from the warehouse into Postgres.

options:
  -h, --help            show this help message and exit
  -d DAY, --day DAY     Day to copy, YYYY-MM-DD
  -t TABLES, --tables TABLES
                        Table to copy, repeat for several; all by default
  --mode {INCREMENTAL,FULL}
                        (default: INCREMENTAL)
  --dry-run, --no-dry-run
                        Read the changes, write nothing (default: False)
```

错误的命令行会在连接任何客户端之前被拒绝，退出码为 `2`：

```console
$ python sync.py -d 2026-10-01 --mode full
usage: sync.py [-h] -d DAY [-t TABLES] [--mode {INCREMENTAL,FULL}]
               [--dry-run | --no-dry-run]
sync.py: error: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
Run sync.sync failed: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
$ echo $?
2
```

`@worker` 对一直运行到收到 SIGTERM 的进程做同样的事，并且会优雅关闭。两者都在
[worker 与 job](zh-CN/workers-and-jobs.md) 中介绍。

## <a id="fastapi"></a>FastAPI

路径操作通过类型提示接收客户端，每个处理函数都不需要 `Depends`，也不需要 `inject()`。
`app/clients.py` 中是[快速开始](#quick-start)里的 `Database` 和 `UserService` 类，不包括其中的 `main()`：

```bash
pip install "nuke-di[fastapi]"
```

```python
# app/api.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di.fastapi import setup

app = FastAPI()
setup(app)  # before the routes: clients connect on startup, disconnect on shutdown


@app.get("/users/{user_id}")
async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


@app.get("/me")
async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user
```

```console
$ uvicorn app.api:app
INFO:     Started server process [55625]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51602 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51604 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [55625]
```

```console
$ curl localhost:8000/users/42
"Hello, user-42!"
$ curl localhost:8000/me -H "X-User-Id: 7"
"user-7"
```

客户端在启动时连接、在关闭时断开，像 `current_user` 这样的依赖项也以同样的方式接收客户端。
导入应用不会构建任何东西，因此测试可以在 `TestClient` 启动应用之前替换客户端：

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
1 passed in 0.23s
```

路由器、WebSocket 和应用自己的 lifespan 在 [FastAPI](zh-CN/fastapi.md) 中介绍；
[Litestar](zh-CN/litestar.md) 和 [FastStream](zh-CN/faststream.md) 的用法相同。

## <a id="documentation"></a>文档

- [客户端](zh-CN/clients.md)：`Client` 与 `NotSingletonClient`、生命周期、dataclass 客户端、
  连接顺序、启动耗时、依赖图、连接错误与解析错误
- [容器](zh-CN/container.md)：`Dependencies` 与全局 `DI`、`resolve()`、`inject()`、
  `mock()`、`override()`
- [worker 与 job](zh-CN/workers-and-jobs.md)：`@job` 与 `@worker`、命令行参数、
  `Shutdown`、宽限期、后台任务、退出码、钩子、Kubernetes
- 框架：[FastAPI](zh-CN/fastapi.md)、[Litestar](zh-CN/litestar.md)、
  [FastStream](zh-CN/faststream.md)，用于 grpc.aio、aiohttp、websockets、APScheduler、
  Textual 和 Temporal 的 [worker 中的服务器](zh-CN/servers-in-workers.md)，以及用 `nuke_di.integration`
  为其他框架[编写集成](zh-CN/integrations.md)
- [测试](zh-CN/testing.md)：`mock()`、`override()`、pytest fixture、检查装配
- [配置](zh-CN/configuration.md)：超时、并发和宽限期
- [错误](zh-CN/errors.md)：每种异常及其抛出时机
- [示例](../../examples/README.md)：21 个可直接运行的场景，从一次性脚本、队列 worker
  到 FastAPI、Litestar、FastStream、Starlette 和完整的服务，每个都附带输出和测试
- [基准测试](../benchmarks.md)：每个场景、Python 3.11–3.14 上的基线，以及与其他库的
  对比
- [编程智能体](zh-CN/agents.md)：Agent Skill、`AGENTS.md` 片段、
  [`llms.txt`](https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms.txt)、Context7、JSON 格式的依赖图
- [开发](zh-CN/development.md)：检查、覆盖率和发布

## <a id="license"></a>许可证

[MIT](../../LICENSE)
