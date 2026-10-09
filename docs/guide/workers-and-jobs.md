# Workers and jobs

**English** · [Русский](../i18n/ru/workers-and-jobs.md) · [简体中文](../i18n/zh-CN/workers-and-jobs.md) · [Español](../i18n/es/workers-and-jobs.md) · [Português (Brasil)](../i18n/pt-BR/workers-and-jobs.md) · [日本語](../i18n/ja/workers-and-jobs.md) · [Polski](../i18n/pl/workers-and-jobs.md)

← [Documentation](../../README.md#documentation)

An async function becomes the main program of a process with one decorator:

| Decorator | Runs                                                   |
|-----------|--------------------------------------------------------|
| `@job`    | Once: the process exits when the function returns      |
| `@worker` | Until the process receives SIGTERM or SIGINT           |

The examples in this section share one module of clients:

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

## Your first job

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

That is the whole program: no `main()`, no `asyncio.run()`, no `if __name__ == "__main__"`.
The process resolves the clients from the global `DI` container, connects them, runs the
function, disconnects them and exits with an [exit code](#exit-codes). Scheduling is not part
of the library: a Kubernetes CronJob, a systemd timer or crontab decides when a job runs.

`nuke-di` logs every run under the `nuke_di` logger. Configure logging above the decorator to
see it:

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

### One entrypoint per module, defined last

When the module is run as `__main__`, the decorator runs the function right away and the
process exits there:

```python
# app/jobs/sync.py
DI.mock(Warehouse, FakeWarehouse())  # runs: code above the decorator is fine


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None: ...


print("never printed")  # never runs under `python -m app.jobs.sync`
```

**Keep one entrypoint per module and define it last.** On a normal import, e.g. from a test,
the decorator returns the function unchanged and nothing runs. The decorated function must be
declared with `async def`, otherwise `TypeError` is raised on import.

## Parameters

Every annotated argument that is not a client becomes a command-line option. Here is the same
job, now able to copy any day, a subset of tables, in a dry run:

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

`pg` and `warehouse` are clients and are injected; `day`, `tables`, `mode` and `dry_run` come
from the command line:

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

`--help` is generated from the signature and the docstring. It does not connect anything
(Python 3.13+ prints `-d, --day DAY` instead of `-d DAY, --day DAY`):

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

A wrong command line is rejected **before any client is resolved or connected**, with exit
code `2`:

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

The first two lines of each error are printed by `argparse`; the `Run ... failed` line is the
`ERROR` record of the `nuke_di` logger, so it follows your logging configuration.
Abbreviations are not accepted: `--dry` is not taken for `--dry-run`.

### Supported types

| Annotation                                    | Command line                    | Example                         |
|-----------------------------------------------|---------------------------------|---------------------------------|
| `str`, `int`, `float`, `pathlib.Path`         | `--name VALUE`                  | `--limit 10`                    |
| `bool`                                        | `--name` / `--no-name`          | `--dry-run`                     |
| `datetime.date`, `datetime.datetime`          | ISO 8601                        | `--since 2026-10-01T12:00:00`   |
| an `Enum`                                     | the member **name**, as written | `--mode FULL`                   |
| `list[T]` of any of the above except `bool`   | the option repeated             | `--table users --table orders`  |
| `T \| None`                                   | as `T`                          | `--limit 10`                    |

The rules:

- **The name.** The option is named after the argument, with `_` replaced by `-`:
  `dry_run` is `--dry-run`. There are no positional arguments, so adding a parameter never
  breaks an existing command line.
- **Required or not.** An argument without a default is a required option. An argument with a
  default is optional, and when the option is left out the function's own default is used.
- **`Option`.** `Annotated[T, Option(help=..., short=...)]` adds a help text and a one-letter
  alias such as `-d`. Both are optional.
- **No parameters.** An entrypoint without parameters still parses its command line: it
  answers `--help` and rejects any argument with exit code `2`.

These signatures are bugs in the code rather than in the command line. They fail the run with
`InvalidSignatureError` and exit code `1`:

```python
async def sync(day: dict[str, int]) -> None: ...  # unsupported type
async def sync(pg: Annotated[Postgres, Option(help="...")]) -> None: ...  # Option on a client
async def sync(help: bool = False) -> None: ...  # clashes with --help
async def sync(day: int, /) -> None: ...  # positional-only
```

### Parameters in tests

The decorated function is still an ordinary coroutine, so a test passes parameters as keyword
arguments:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    warehouse.changes.assert_awaited_once_with("users", datetime.date(2026, 10, 1))
    pg.upsert.assert_awaited_once_with("users", ["row"])
```

## Your first worker

A worker runs until the process is asked to stop. It depends on the `Shutdown` client, which
is set on the first SIGTERM or SIGINT, and finishes its current piece of work:

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

Ctrl+C in the middle of the third message: the message is finished, the loop ends, the
clients disconnect.

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

`Shutdown` has three methods:

| Method           | Description                                                               |
|------------------|---------------------------------------------------------------------------|
| `is_set()`       | Whether a Shutdown has begun; check it between pieces of work             |
| `await wait()`   | Block until a Shutdown begins                                             |
| `set()`          | Begin a Shutdown by hand, e.g. in a test                                  |

Outside a worker or a job nothing sets it, so a loop that depends on `Shutdown` also works
unchanged inside a web application. A worker that returns or raises on its own ends the
process too: restarting it is the orchestrator's job.

On Windows only SIGINT (Ctrl+C) is handled; SIGTERM keeps its default behavior.

## Grace period

A worker that ignores `Shutdown` is cancelled after `SHUTDOWN_GRACE_SECONDS` (default `10`):

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

A second signal cancels the entrypoint at once, without waiting for the grace period, e.g.
Ctrl+C twice:

```console
$ python -m app.workers.stubborn
queue: connected
stubborn: processing message-1
^C^C
Second SIGINT, cancelling run app.workers.stubborn.stubborn
queue: disconnected
```

A signal that arrives while the clients are still connecting stops the startup, and the
clients that already connected are disconnected.

In the worst case a process stops in
`SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × the longest chain of dependencies`. With
the defaults, a chain of two clients takes the whole Kubernetes default `terminationGracePeriodSeconds` of 30 seconds,
so lower the timeouts or raise the grace period for deeper trees.

## Background tasks

`BackgroundTasks` is a client that supervises coroutines running alongside the entrypoint.
Unlike a bare `asyncio.create_task()`, a failing task is never lost: it is logged with its
traceback and fails the whole process.

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

The worker was cancelled without a grace period: a crashed background loop must not leave a
live process that does nothing. When the process stops for any reason, the tasks are cancelled
and awaited **before** any client disconnects, so they never run against closed clients.

| Method                     | Description                                                     |
|----------------------------|-----------------------------------------------------------------|
| `spawn(coro, name=None)`   | Start `coro` as a task and keep a reference to it until it ends |
| `watch(callback)`          | Call `callback(exc)` for every task that fails                  |
| `await stop()`             | Cancel every task and wait for all of them; `disconnect()` calls it |

Outside a worker or a job, e.g. under a plain `async with DI`, failures are only logged and
the tasks are cancelled on `disconnect()`.

## Exit codes

The first matching rule wins:

| Condition                                                                                           | Exit code      |
|-----------------------------------------------------------------------------------------------------|----------------|
| An invalid command line (`UsageError`)                                                              | `2`            |
| An exception: the signature, resolving or connecting the clients, the entrypoint, a background task | `1`            |
| A termination signal was received                                                                   | `128 + signum` |
| Otherwise                                                                                           | `0`            |

SIGTERM gives `143` and SIGINT gives `130`. A job that sees a Shutdown and returns cleanly
still exits with `128 + signum`: its work was interrupted, and a scheduler must not count it
as complete.

The codes are meant for whatever starts the process:

```bash
python -m app.jobs.sync --day 2026-10-01
case $? in
  0)       echo "synced" ;;
  2)       echo "fix the command line, retrying will not help" ;;
  130|143) echo "interrupted, safe to run again" ;;
  *)       echo "failed, see the log" ;;
esac
```

## Hooks

Hooks observe every run, e.g. to push metrics or open a tracing span:

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

`on_start` is called in list order before the clients are resolved; `on_finish` in reverse
order after they have disconnected, so it sees the final state of the `Run`, connect failures
included:

| `Run` field   | Value                                                                  |
|---------------|------------------------------------------------------------------------|
| `name`        | Module and function, e.g. `app.jobs.report.report`                     |
| `kind`        | `"job"` or `"worker"`                                                  |
| `started_at`  | UTC `datetime`                                                         |
| `finished_at` | UTC `datetime`, set before `on_finish`                                 |
| `exit_code`   | The process exit code, set before `on_finish`                          |
| `error`       | The exception that failed the run, e.g. a `UsageError`, or `None`      |
| `signal`      | The first termination signal received, or `None`                       |
| `clients`     | One `ClientTiming` per client: connect and disconnect durations and outcomes; empty if the run failed before connecting |

Hooks are plain objects, not clients: they manage their own resources. An exception in a hook
is logged and does not change the exit code. `--help` is not a run, so hooks do not see it.

### Startup metrics and structured logs

`run.clients` is the place to export startup metrics: `on_finish` sees how long every
client took to connect and disconnect. Every log record of `nuke_di` also carries
structured fields, so a JSON formatter can filter and aggregate by client without
parsing messages:

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

| Field      | Set on                                                                        |
|------------|-------------------------------------------------------------------------------|
| `run`      | Every record made inside a worker or a job, the container's included: the name of the run |
| `client`   | Every record about one client: resolving, connecting, disconnecting, failures |
| `duration` | Seconds: a connected or disconnected client, the startup summary, a finished run |

Every run also connects its own `Shutdown` and `BackgroundTasks` clients, so they appear
in `run.clients` and in the summary.

## Running in Kubernetes

A job maps to a CronJob and a worker to a Deployment. Give a worker enough
`terminationGracePeriodSeconds` for the [shutdown budget](#grace-period):

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

A one-off backfill is the same image with other parameters:

```bash
kubectl run sync-backfill --rm -it --restart=Never --image=registry.example.com/app:1.0 \
  --command -- python -m app.jobs.sync --day 2026-09-30 --mode FULL
```
