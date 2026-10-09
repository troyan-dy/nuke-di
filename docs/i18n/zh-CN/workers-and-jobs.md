# <a id="workers-and-jobs"></a>worker 与 job

[English](../../guide/workers-and-jobs.md) · [Русский](../ru/workers-and-jobs.md) · **简体中文** · [Español](../es/workers-and-jobs.md) · [Português (Brasil)](../pt-BR/workers-and-jobs.md) · [日本語](../ja/workers-and-jobs.md) · [Polski](../pl/workers-and-jobs.md)

← [文档](../README.zh-CN.md#documentation)

只需一个装饰器，异步函数就能成为一个进程的主程序：

| 装饰器    | 运行方式                                               |
|-----------|--------------------------------------------------------|
| `@job`    | 运行一次：函数返回时进程退出                           |
| `@worker` | 一直运行，直到进程收到 SIGTERM 或 SIGINT               |

本节的示例共用同一个客户端模块：

```python
# app/clients.py
import datetime
import itertools

from nuke_di import Client


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")

    async def upsert(self, table: str, rows: list[str]) -> None:
        print(f"postgres: upserted {len(rows)} rows into {table}")


class Warehouse(Client):
    async def connect(self) -> None:
        print("warehouse: connected")

    async def disconnect(self) -> None:
        print("warehouse: disconnected")

    async def changes(self, table: str, day: datetime.date) -> list[str]:
        return [f"{table}:{day}:{n}" for n in range(3)]


class Queue(Client):
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    async def connect(self) -> None:
        print("queue: connected")

    async def disconnect(self) -> None:
        print("queue: disconnected")

    async def get(self) -> str:
        return f"message-{next(self._ids)}"
```

## <a id="your-first-job"></a>第一个 job

```python
# app/jobs/sync.py
import datetime

from nuke_di import job

from app.clients import Postgres, Warehouse


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None:
    day = datetime.date.today() - datetime.timedelta(days=1)
    for table in ["users", "orders"]:
        await pg.upsert(table, await warehouse.changes(table, day))
```

```console
$ python -m app.jobs.sync
postgres: connected
warehouse: connected
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
$ echo $?
0
```

这就是整个程序：没有 `main()`，没有 `asyncio.run()`，也没有 `if __name__ == "__main__"`。
进程从全局容器 `DI` 中解析客户端、连接它们、运行函数、断开连接，然后以相应的[退出码](#exit-codes)退出。
调度不在本库的职责范围内：job 何时运行，由 Kubernetes CronJob、systemd timer 或 crontab 决定。

`nuke-di` 通过 `nuke_di` logger 记录每一次运行。在装饰器上方配置好 logging，
就能看到这些日志：

```python
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s: %(message)s")


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None: ...
```

```console
$ python -m app.jobs.sync
INFO  nuke_di.run: Starting job app.jobs.sync.sync
postgres: connected
warehouse: connected
INFO  nuke_di.core: Connected 4 clients in 0.00s (slowest: Postgres 0.00s, Shutdown 0.00s, Warehouse 0.00s)
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
INFO  nuke_di.run: Run app.jobs.sync.sync finished with exit code 0 in 0.002s
```

### <a id="one-entrypoint-per-module-defined-last"></a>每个模块一个入口点，并放在最后定义

当模块以 `__main__` 身份运行时，装饰器会立即运行该函数，进程也就在那里退出：

```python
# app/jobs/sync.py
DI.mock(Warehouse, FakeWarehouse())  # runs: code above the decorator is fine


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None: ...


print("never printed")  # never runs under `python -m app.jobs.sync`
```

**每个模块只保留一个入口点，并把它定义在最后。** 正常导入时（例如在测试中导入），
装饰器会原样返回函数，什么也不会运行。被装饰的函数必须用 `async def` 声明，
否则导入时会抛出 `TypeError`。

## <a id="parameters"></a>参数

每个不是客户端的带注解参数都会成为一个命令行选项。下面还是同一个 job，现在它可以复制任意一天的数据、
只复制部分表，还支持试运行（dry run）：

```python
# app/jobs/sync.py
import datetime
import enum
from typing import Annotated

from nuke_di import Option, job

from app.clients import Postgres, Warehouse


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

`pg` 和 `warehouse` 是客户端，会被注入；`day`、`tables`、`mode` 和 `dry_run`
则来自命令行：

```console
$ python -m app.jobs.sync --day 2026-10-01
postgres: connected
warehouse: connected
sync: INCREMENTAL copy of 2026-10-01
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected

$ python -m app.jobs.sync -d 2026-10-01 -t users --mode FULL --dry-run
postgres: connected
warehouse: connected
sync: FULL copy of 2026-10-01
sync: would upsert 3 rows into users
postgres: disconnected
warehouse: disconnected
```

`--help` 根据函数签名和 docstring 自动生成，并且不会连接任何东西
（Python 3.13+ 输出的是 `-d, --day DAY`，而不是 `-d DAY, --day DAY`）：

```console
$ python -m app.jobs.sync --help
usage: python -m app.jobs.sync [-h] -d DAY [-t TABLES]
                               [--mode {INCREMENTAL,FULL}]
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

错误的命令行会**在解析或连接任何客户端之前**被拒绝，
退出码为 `2`：

```console
$ python -m app.jobs.sync
usage: python -m app.jobs.sync [-h] -d DAY [-t TABLES]
                               [--mode {INCREMENTAL,FULL}]
                               [--dry-run | --no-dry-run]
python -m app.jobs.sync: error: the following arguments are required: -d/--day
Run app.jobs.sync.sync failed: the following arguments are required: -d/--day
$ echo $?
2

$ python -m app.jobs.sync --day yesterday
...
python -m app.jobs.sync: error: argument -d/--day: invalid date value: 'yesterday'

$ python -m app.jobs.sync -d 2026-10-01 --mode full
...
python -m app.jobs.sync: error: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)

$ python -m app.jobs.sync -d 2026-10-01 --dry
...
python -m app.jobs.sync: error: unrecognized arguments: --dry
```

每条错误的前两行由 `argparse` 输出；`Run ... failed` 这一行是 `nuke_di` logger 的
`ERROR` 记录，因此遵循你的 logging 配置。
不接受缩写：`--dry` 不会被当作 `--dry-run`。

### <a id="supported-types"></a>支持的类型

| 注解                                          | 命令行                          | 示例                            |
|-----------------------------------------------|---------------------------------|---------------------------------|
| `str`, `int`, `float`, `pathlib.Path`         | `--name VALUE`                  | `--limit 10`                    |
| `bool`                                        | `--name` / `--no-name`          | `--dry-run`                     |
| `datetime.date`, `datetime.datetime`          | ISO 8601                        | `--since 2026-10-01T12:00:00`   |
| 任意 `Enum`                                   | 成员的**名称**，与定义完全一致  | `--mode FULL`                   |
| 由上述任一类型（`bool` 除外）构成的 `list[T]` | 重复使用该选项                  | `--table users --table orders`  |
| `T \| None`                                   | 与 `T` 相同                     | `--limit 10`                    |

规则如下：

- **名称。** 选项以参数名命名，并把 `_` 替换为 `-`：
  `dry_run` 对应 `--dry-run`。不存在位置参数，因此新增参数永远不会
  破坏现有的命令行。
- **是否必需。** 没有默认值的参数是必需选项。有默认值的参数是可选的，
  省略该选项时使用函数自身的默认值。
- **`Option`。** `Annotated[T, Option(help=..., short=...)]` 用于添加帮助文本和单字母别名，
  例如 `-d`。两者都是可选的。
- **没有参数。** 没有参数的入口点仍会解析命令行：它会响应
  `--help`，并以退出码 `2` 拒绝任何参数。

以下签名属于代码中的错误，而不是命令行的错误。它们会让这次运行以
`InvalidSignatureError` 和退出码 `1` 失败：

```python
async def sync(day: dict[str, int]) -> None: ...  # unsupported type
async def sync(pg: Annotated[Postgres, Option(help="...")]) -> None: ...  # Option on a client
async def sync(help: bool = False) -> None: ...  # clashes with --help
async def sync(day: int, /) -> None: ...  # positional-only
```

### <a id="parameters-in-tests"></a>在测试中传入参数

被装饰的函数仍是一个普通的协程，因此测试可以通过关键字参数
传入参数：

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    warehouse.changes.assert_awaited_once_with("users", datetime.date(2026, 10, 1))
    pg.upsert.assert_awaited_once_with("users", ["row"])
```

## <a id="your-first-worker"></a>第一个 worker

worker 会一直运行，直到进程被要求停止。它依赖客户端 `Shutdown`，该客户端在收到第一个
SIGTERM 或 SIGINT 时被置位，worker 随后处理完手头的工作再结束：

```python
# app/workers/consumer.py
import asyncio

from nuke_di import Shutdown, worker

from app.clients import Queue


@worker
async def consumer(queue: Queue, shutdown: Shutdown) -> None:
    while not shutdown.is_set():
        message = await queue.get()
        print(f"consumer: processing {message}")
        await asyncio.sleep(1)  # the actual work
        print(f"consumer: done {message}")
    print("consumer: stopped")
```

在处理第三条消息的中途按下 Ctrl+C：这条消息会被处理完，循环结束，
客户端断开连接。

```console
$ python -m app.workers.consumer
queue: connected
consumer: processing message-1
consumer: done message-1
consumer: processing message-2
consumer: done message-2
consumer: processing message-3
^C
consumer: done message-3
consumer: stopped
queue: disconnected
$ echo $?
130
```

`Shutdown` 有三个方法：

| 方法             | 说明                                                                      |
|------------------|---------------------------------------------------------------------------|
| `is_set()`       | 是否已开始关闭；在两段工作之间检查它                                      |
| `await wait()`   | 阻塞，直到开始关闭                                                        |
| `set()`          | 手动发起关闭，例如在测试中                                                |

在 worker 或 job 之外，没有任何东西会将它置位，因此依赖 `Shutdown` 的循环放进 Web 应用里也能
原样工作。自行返回或抛出异常的 worker 同样会结束进程：
重启它是编排系统的职责。

在 Windows 上只处理 SIGINT（Ctrl+C）；SIGTERM 保持默认行为。

## <a id="grace-period"></a>宽限期

忽略 `Shutdown` 的 worker 会在 `SHUTDOWN_GRACE_SECONDS`（默认 `10`）之后被取消：

```python
# app/workers/stubborn.py
@worker
async def stubborn(queue: Queue) -> None:
    while True:  # never looks at Shutdown
        message = await queue.get()
        print(f"stubborn: processing {message}")
        await asyncio.sleep(5)
```

```console
$ SHUTDOWN_GRACE_SECONDS=2 python -m app.workers.stubborn &
queue: connected
stubborn: processing message-1
$ kill -TERM %1
Run app.workers.stubborn.stubborn did not stop within 2.0s after Shutdown, cancelling it
queue: disconnected
$ wait %1; echo $?
143
```

第二个信号会立即取消入口点，不再等待宽限期结束，例如
连按两次 Ctrl+C：

```console
$ python -m app.workers.stubborn
queue: connected
stubborn: processing message-1
^C^C
Second SIGINT, cancelling run app.workers.stubborn.stubborn
queue: disconnected
```

如果信号在客户端仍在连接时到达，启动过程会停止，
已经连接的客户端会被断开。

最坏情况下，进程停止需要
`SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × 最长依赖链的长度`。在默认设置下，由两个客户端组成的依赖链就会
用完 Kubernetes 默认的 30 秒 `terminationGracePeriodSeconds`，
因此对于更深的依赖树，请调低超时或调高宽限期。

## <a id="background-tasks"></a>后台任务

`BackgroundTasks` 是一个客户端，负责监管与入口点并行运行的协程。
与直接调用 `asyncio.create_task()` 不同，失败的任务永远不会被悄悄丢掉：它会连同 traceback 一起记录到日志，
并让整个进程失败。

```python
# app/workers/indexer.py
import asyncio

from nuke_di import BackgroundTasks, Shutdown, worker

from app.clients import Queue


async def refresh_index() -> None:
    for attempt in range(1, 10):
        print(f"refresh: run {attempt}")
        await asyncio.sleep(0.5)
        if attempt == 2:
            raise ConnectionError("search cluster is unreachable")


@worker
async def indexer(queue: Queue, tasks: BackgroundTasks, shutdown: Shutdown) -> None:
    tasks.spawn(refresh_index(), name="refresh-index")
    print("indexer: waiting for Shutdown")
    await shutdown.wait()
```

```console
$ python -m app.workers.indexer
queue: connected
indexer: waiting for Shutdown
refresh: run 1
refresh: run 2
Background task refresh-index failed
Traceback (most recent call last):
  ...
ConnectionError: search cluster is unreachable
queue: disconnected
Run app.workers.indexer.indexer failed
Traceback (most recent call last):
  ...
ConnectionError: search cluster is unreachable
$ echo $?
1
```

worker 被取消时没有宽限期：崩溃的后台循环不应留下一个存活却什么也不做的
进程。无论进程因何停止，任务都会在任何客户端断开连接**之前**被取消并等待结束，
因此它们绝不会在已关闭的客户端上运行。

| 方法                       | 说明                                                            |
|----------------------------|-----------------------------------------------------------------|
| `spawn(coro, name=None)`   | 将 `coro` 作为任务启动，并持有它的引用直到它结束                |
| `watch(callback)`          | 对每个失败的任务调用 `callback(exc)`                            |
| `await stop()`             | 取消所有任务并等待它们全部结束；`disconnect()` 会调用它         |

在 worker 或 job 之外，例如在普通的 `async with DI` 下，失败只会被记录到日志，
任务在 `disconnect()` 时被取消。

## <a id="exit-codes"></a>退出码

按顺序匹配，第一条命中的规则生效：

| 条件                                                                                                | 退出码         |
|-----------------------------------------------------------------------------------------------------|----------------|
| 无效的命令行（`UsageError`）                                                                        | `2`            |
| 出现异常：签名、客户端的解析或连接、入口点本身、后台任务                                            | `1`            |
| 收到了终止信号                                                                                      | `128 + signum` |
| 其他情况                                                                                            | `0`            |

SIGTERM 对应 `143`，SIGINT 对应 `130`。job 察觉到关闭后即使正常返回，
退出码仍是 `128 + signum`：它的工作被中断了，调度器不应把它
视为已完成。

这些退出码是给负责启动进程的一方使用的：

```bash
python -m app.jobs.sync --day 2026-10-01
case $? in
  0)       echo "synced" ;;
  2)       echo "fix the command line, retrying will not help" ;;
  130|143) echo "interrupted, safe to run again" ;;
  *)       echo "failed, see the log" ;;
esac
```

## <a id="hooks"></a>钩子

钩子可以观察每一次运行，例如用来上报指标或开启一个 tracing span：

```python
# app/jobs/report.py
from nuke_di import Run, job

from app.clients import Postgres


class Timer:
    async def on_start(self, run: Run) -> None:
        print(f"hook: {run.kind} {run.name} started")

    async def on_finish(self, run: Run) -> None:
        seconds = (run.finished_at - run.started_at).total_seconds()
        print(f"hook: exit code {run.exit_code} in {seconds:.1f}s, error: {run.error!r}")


@job(hooks=[Timer()])
async def report(pg: Postgres, limit: int = 10) -> None:
    print(f"report: top {limit} customers")
```

```console
$ python -m app.jobs.report --limit 3
hook: job app.jobs.report.report started
postgres: connected
report: top 3 customers
postgres: disconnected
hook: exit code 0 in 0.0s, error: None

$ python -m app.jobs.report --limit three
usage: python -m app.jobs.report [-h] [--limit LIMIT]
python -m app.jobs.report: error: argument --limit: invalid int value: 'three'
hook: job app.jobs.report.report started
Run app.jobs.report.report failed: argument --limit: invalid int value: 'three'
hook: exit code 2 in 0.0s, error: UsageError("argument --limit: invalid int value: 'three'")
```

`on_start` 在解析客户端之前按列表顺序调用；`on_finish` 在客户端断开连接之后按逆序调用，
因此它能看到 `Run` 的最终状态，连接失败也包括在内：

| `Run` 字段    | 值                                                                     |
|---------------|------------------------------------------------------------------------|
| `name`        | 模块和函数，例如 `app.jobs.report.report`                              |
| `kind`        | `"job"` 或 `"worker"`                                                  |
| `started_at`  | UTC `datetime`                                                         |
| `finished_at` | UTC `datetime`，在 `on_finish` 之前设置                                |
| `exit_code`   | 进程的退出码，在 `on_finish` 之前设置                                  |
| `error`       | 导致运行失败的异常，例如 `UsageError`；否则为 `None`                   |
| `signal`      | 收到的第一个终止信号；否则为 `None`                                    |
| `clients`     | 每个客户端一个 `ClientTiming`：连接和断开的耗时与结果；运行在连接前失败时为空 |

钩子是普通对象，不是客户端：它们自行管理自己的资源。钩子中的异常
会被记录到日志，但不会改变退出码。`--help` 不算一次运行，因此钩子看不到它。

### <a id="startup-metrics-and-structured-logs"></a>启动指标与结构化日志

`run.clients` 是导出启动指标的地方：`on_finish` 能看到每个客户端连接和断开各用了多久。
此外，`nuke_di` 的每条日志记录都带有结构化字段，JSON formatter 无需解析消息文本即可按客户端
过滤和聚合：

```python
# app/jobs/startup.py
import json
import logging

from nuke_di import Run, job

from app.clients import Postgres, Warehouse


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {key: getattr(record, key) for key in ("run", "client", "duration") if hasattr(record, key)}
        return json.dumps({"level": record.levelname, "message": record.getMessage(), **fields})


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])


class StartupMetrics:
    async def on_start(self, run: Run) -> None:
        pass

    async def on_finish(self, run: Run) -> None:
        for client in run.clients:
            print(f"metric: {client.name} connect={client.connect:.3f}s {client.connect_outcome}")


@job(hooks=[StartupMetrics()])
async def startup(pg: Postgres, warehouse: Warehouse) -> None:
    print("startup: done")
```

```console
$ python -m app.jobs.startup
{"level": "INFO", "message": "Starting job app.jobs.startup.startup", "run": "app.jobs.startup.startup"}
postgres: connected
warehouse: connected
{"level": "INFO", "message": "Connected 4 clients in 0.00s (slowest: Postgres 0.00s, Shutdown 0.00s, Warehouse 0.00s)", "run": "app.jobs.startup.startup", "duration": 0.00022179202642291784}
startup: done
postgres: disconnected
warehouse: disconnected
{"level": "INFO", "message": "Run app.jobs.startup.startup finished with exit code 0 in 0.001s", "run": "app.jobs.startup.startup", "duration": 0.001171}
metric: Shutdown connect=0.000s ok
metric: BackgroundTasks connect=0.000s ok
metric: Postgres connect=0.000s ok
metric: Warehouse connect=0.000s ok
```

| 字段       | 出现在                                                                        |
|------------|-------------------------------------------------------------------------------|
| `run`      | 在 worker 或 job 内产生的每条记录，包括容器的记录：运行的名称                 |
| `client`   | 关于单个客户端的每条记录：解析、连接、断开、失败                              |
| `duration` | 秒数：已连接或已断开的客户端、启动汇总、已结束的运行                          |

每次运行还会连接它自己的 `Shutdown` 和 `BackgroundTasks` 客户端，所以它们也会出现在
`run.clients` 和汇总中。

## <a id="running-in-kubernetes"></a>在 Kubernetes 中运行

job 对应 CronJob，worker 对应 Deployment。请给 worker 留出足够的
`terminationGracePeriodSeconds`，以满足[关闭所需的时间](#grace-period)：

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: report
spec:
  schedule: "0 6 * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: report
              image: registry.example.com/app:1.0
              command: ["python", "-m", "app.jobs.report"]
              args: ["--limit", "20"]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: consumer
spec:
  replicas: 2
  selector:
    matchLabels: {app: consumer}
  template:
    metadata:
      labels: {app: consumer}
    spec:
      terminationGracePeriodSeconds: 30  # >= SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × longest chain
      containers:
        - name: consumer
          image: registry.example.com/app:1.0
          command: ["python", "-m", "app.workers.consumer"]
          env:
            - {name: SHUTDOWN_GRACE_SECONDS, value: "15"}
```

一次性的回填只需使用同一个镜像，换一组参数：

```bash
kubectl run sync-backfill --rm -it --restart=Never --image=registry.example.com/app:1.0 \
  --command -- python -m app.jobs.sync --day 2026-09-30 --mode FULL
```
