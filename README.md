# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](#development)
[![License](https://img.shields.io/pypi/l/nuke-di)](LICENSE)

The simplest dependency injection for async Python projects.

Dependencies are declared with plain type hints. `nuke-di` builds the dependency tree,
creates every client once and drives its async lifecycle: `connect()` on startup and
`disconnect()` on shutdown. Independent clients start concurrently, layer by layer,
from the deepest dependencies up.

It was extracted from the DI layer of a production Python microservice framework
and has no runtime dependencies.

## Installation

```bash
pip install nuke-di
```

Requires Python 3.11+.

## Quick start

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

## Concepts

### Clients

Every dependency is a subclass of one of two base classes:

| Base class           | Instances                                          |
|----------------------|----------------------------------------------------|
| `Client`             | Singleton: one instance per `Dependencies`         |
| `NotSingletonClient` | A new instance for every consumer that declares it |

Override the async `connect()` / `disconnect()` methods to open and release resources
such as connection pools:

```python
class Redis(Client):
    def __init__(self) -> None:
        self._pool: Pool | None = None

    async def connect(self) -> None:
        self._pool = await create_pool()

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
```

### Composition

A client declares its own dependencies in `__init__`. Only arguments annotated with a
client type are injected; resolution is recursive.

```python
class BusinessLogic(Client):
    def __init__(self, pg: Postgres, grpc: GrpcClient) -> None:
        self._pg = pg
        self._grpc = grpc
```

### Layers

Clients connect concurrently in layers. Clients without dependencies form layer 0;
every other client sits one layer above its highest dependency. A layer starts only
after the previous one has connected, so a client never connects before its own
dependencies. `disconnect()` walks the layers in reverse.

```text
Checkout(pg: Postgres, payments: Payments)    layer 2
Payments(pg: Postgres)                        layer 1
Postgres, Redis                               layer 0  <- connect together
```

Only dependencies declared in `__init__` are ordered. If a client needs another one to be
connected first, declare it as a dependency.

If a client fails to connect, the rest of its layer is cancelled and the next layers
never start. The clients that already connected are disconnected, layers in reverse, and
the container is left disconnected and empty; the same happens when `connect()` itself is
cancelled. Mocked clients are not connected and do not affect the layers.

### Container

`Dependencies` is the container. `DI` is a ready-to-use global instance; create your own
when you need isolation, e.g. in tests.

| Method               | Description                                                             |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Build `cls` and its dependency tree. Idempotent for `Client`.           |
| `inject(func)`       | Return `functools.partial(func, ...)` with client arguments bound. Every argument of `func` except `*args` / `**kwargs` must have a type hint. |
| `connect()`          | Call `connect()` on every resolved client, layer by layer.              |
| `disconnect()`       | Call `disconnect()` layer by layer in reverse, then `flush()` the container. |
| `async with`         | `connect()` on enter, `disconnect()` on exit.                           |
| `mock(cls, new=None)`| Register a replacement for `cls` (an autospec mock by default).         |
| `flush()`            | Forget every resolved client.                                           |

`resolve`, `inject`, `mock` and `flush` only work while the container is disconnected:
the whole tree is built before startup.

A failing or hanging `disconnect()` is logged and does not stop the other clients from
shutting down: each one is bounded by `DISCONNECT_TIMEOUT_SECONDS`.

### Dataclass clients

`client_dataclass` turns a class into a `Client` and a dataclass at once, so the fields
become the injected dependencies:

```python
from nuke_di import client_dataclass


@client_dataclass(frozen=True)
class Checkout:
    pg: Postgres
    payments: PaymentsClient
```

It accepts the same keyword arguments as `dataclasses.dataclass`.

## Workers and jobs

An async function becomes the main program of a process with one decorator:

| Decorator | Runs                                                   |
|-----------|--------------------------------------------------------|
| `@job`    | Once: the process exits when the function returns      |
| `@worker` | Until the process receives SIGTERM or SIGINT           |

```python
# app/jobs/sync.py
from nuke_di import job

from app.clients import Postgres, Warehouse


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None:
    for batch in await warehouse.changed_batches():
        await pg.upsert(batch)
```

```bash
python -m app.jobs.sync
```

Every client argument is injected from the global `DI` container; every other argument is a
[parameter](#parameters) read from the command line. The process resolves the clients, connects
them, runs the function, disconnects them and exits with an exit code.
Scheduling is not part of the library: a Kubernetes CronJob, a systemd timer or crontab
decides when a job runs.

When the module is run as `__main__`, the decorator runs the function right away and the
process exits there, so **code below the decorated function never runs: keep one
entrypoint per module and define it last.** On a normal import the decorator returns the
function unchanged, so a test calls it directly with mocks:

```python
from unittest.mock import AsyncMock


async def test_sync() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changed_batches.return_value = [batch]

    await sync(pg, warehouse)

    pg.upsert.assert_awaited_once_with(batch)
```

The decorated function must be declared with `async def`, otherwise `TypeError` is raised on import.

### Parameters

Every annotated argument that is not a client becomes a command-line option:

```python
import datetime
from typing import Annotated

from nuke_di import Option, job


@job
async def sync(
    pg: Postgres,
    date: Annotated[datetime.date, Option(help="Day to sync", short="d")],
    tables: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    """Copy one day of changes into the warehouse."""
```

```bash
python -m app.jobs.sync --date 2026-10-01 --tables users --tables orders --dry-run
python -m app.jobs.sync --help
```

| Annotation                                | Command line                                  |
|-------------------------------------------|-----------------------------------------------|
| `str`, `int`, `float`, `pathlib.Path`     | `--name VALUE`                                |
| `bool`                                    | `--name` / `--no-name`                        |
| `datetime.date`, `datetime.datetime`      | `--name 2026-10-01`, ISO 8601                 |
| an `Enum`                                 | `--name MEMBER`, by member name               |
| `list[T]` of any of the above but `bool`  | repeated: `--name a --name b`                 |
| `T \| None`                               | as `T`                                        |

- The option is named after the argument, with `_` replaced by `-`: `date_from` is `--date-from`.
- An argument without a default is a required option; an argument with a default keeps it when
  the option is not given.
- `Annotated[T, Option(help=..., short=...)]` adds a help text and a one-letter alias.
- The function's docstring is the description in `--help`.

`--help` prints the help and exits with `0` without starting a run. An invalid command line
prints the usage and the error and exits with `2`: no client is resolved or connected, and hooks
see a `UsageError`. An unsupported annotation, `Option` on a client, or two options with the same
flag raise `InvalidSignatureError` and exit with `1`. Every entrypoint parses its command line, so
one without parameters rejects any argument.

A test passes parameters as keyword arguments: `await sync(pg, warehouse, date=datetime.date(2026, 10, 1))`.

### Shutdown

On the first SIGTERM or SIGINT the `Shutdown` client is set. An entrypoint that depends on
it can finish its current piece of work and return:

```python
from nuke_di import Shutdown, worker


@worker
async def consumer(queue: Queue, shutdown: Shutdown) -> None:
    while not shutdown.is_set():
        message = await queue.get()
        await message.process()
        await message.ack()
```

`await shutdown.wait()` blocks until the Shutdown begins. If the entrypoint is still
running after `SHUTDOWN_GRACE_SECONDS`, it is cancelled; a second signal cancels it
immediately. A signal that arrives while the clients are connecting stops the startup, and
the clients that already connected are disconnected.

A worker that returns or raises on its own also ends the process: restarting it is the
orchestrator's job.

In the worst case a process stops in
`SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers`. With the defaults, a tree of
two layers takes the whole Kubernetes default `terminationGracePeriodSeconds` of 30 seconds,
so lower the timeouts or raise the grace period for deeper trees.

On Windows only SIGINT (Ctrl+C) is handled; SIGTERM keeps its default behavior.

### Background tasks

`BackgroundTasks` is a client that supervises coroutines running alongside the entrypoint:

```python
@worker
async def indexer(tasks: BackgroundTasks, search: Search, shutdown: Shutdown) -> None:
    tasks.spawn(search.refresh_loop(), name="refresh")
    await shutdown.wait()
```

A failing task is logged with its traceback, and inside a worker or a job it fails the whole
process: the entrypoint is cancelled and the exit code is `1`. When the process stops, the
tasks are cancelled and awaited **before** any client disconnects, so they never run against
closed clients. Outside a worker or a job, e.g. under a plain `async with DI`, failures are
only logged and the tasks are cancelled on `disconnect()`.

### Exit codes

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

### Hooks

Hooks observe every run, e.g. to push metrics or open a tracing span:

```python
from nuke_di import Run, job


class Metrics:
    async def on_start(self, run: Run) -> None:
        print(f"{run.kind} {run.name} started at {run.started_at}")

    async def on_finish(self, run: Run) -> None:
        print(f"{run.name} exited with {run.exit_code}, error: {run.error!r}")


@job(hooks=[Metrics()])
async def sync(pg: Postgres) -> None: ...
```

`on_start` is called in list order before the clients are resolved; `on_finish` in reverse
order after they have disconnected, so it sees the final `exit_code`, `error`, `signal` and
`finished_at`, connect failures included. Hooks are plain objects, not clients: they manage
their own resources. An exception in a hook is logged and does not change the exit code.

## Testing

Register mocks before the tree is resolved; every consumer then receives the mock.

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

## Configuration

| Environment variable      | Default | Description                                        |
|---------------------------|---------|----------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS` | `30`    | Timeout for a single client's `connect()`, seconds |
| `CONNECT_CONCURRENCY`     | `0`     | How many clients may connect or disconnect at once across the container; `0` means no limit |
| `DISCONNECT_TIMEOUT_SECONDS` | `10` | Timeout for a single client's `disconnect()`, seconds |
| `SHUTDOWN_GRACE_SECONDS`  | `10`    | How long a worker or a job may keep running after SIGTERM / SIGINT before it is cancelled, seconds; read when the process starts |

The container settings are read when a `Dependencies` instance is created. You can also pass them explicitly:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```

## Errors

| Exception                   | Raised when                                               |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | A client's `__init__` raised                              |
| `ConnectError`              | A client's `connect()` raised, or the container state is wrong (e.g. resolving after connect) |
| `ConnectTimeoutError`       | A client's `connect()` exceeded `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError`     | `inject()` got a function with an argument without a type hint, or an entrypoint parameter has an unsupported type or a clashing flag |
| `UsageError`                | The command line of a worker or a job does not match its parameters; recorded as `Run.error`, exit code `2` |

`InitializeDependencyError` and `ConnectError` derive from `SystemExit`: an application
whose dependencies cannot start is expected to stop. Catch them explicitly if you need
different behavior; the original exception is available as `__cause__`.

`nuke-di` logs through the standard `logging` module under the `nuke_di` logger.

## Development

```bash
make install   # uv sync --locked
make check     # ruff, mypy and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

Line and branch coverage is 100%, and CI fails if it drops below that
(`fail_under = 100` in `pyproject.toml`).

## License

[MIT](LICENSE)
