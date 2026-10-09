# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/development.md)
[![License](https://img.shields.io/pypi/l/nuke-di)](LICENSE)

**English** · [Русский](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ru.md) · [简体中文](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.zh-CN.md) · [Español](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.es.md) · [Português (Brasil)](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pt-BR.md) · [日本語](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ja.md) · [Polski](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pl.md)

The simplest dependency injection for async Python projects.

Dependencies are declared with plain type hints. `nuke-di` builds the dependency tree,
creates every client once and drives its async lifecycle: `connect()` on startup and
`disconnect()` on shutdown. Independent clients start concurrently, layer by layer,
from the deepest dependencies up.

On top of that, one decorator turns an async function into a process with command-line
arguments, and FastAPI, Litestar and FastStream handlers take clients by type hint the same way.

It was extracted from the DI layer of a production Python microservice framework
and has no runtime dependencies.

- [Installation](#installation) · [Quick start](#quick-start) · [Principles](#principles) · [Performance](#performance)
- Examples: [a job with command-line arguments](#a-job-with-command-line-arguments) · [FastAPI](#fastapi)
- [Documentation](#documentation)

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

What happened:

1. `DI.inject(handler)` read the type hints of `handler`, found the client `UserService`, saw
   that it needs a `Database` in its `__init__` and built both. `user_id: int` is not a
   client, so it stays a regular argument.
2. `async with DI` called `connect()` on every client it built, dependencies first.
3. `injected(42)` called `handler(42, users=<UserService>)`.
4. Leaving the `async with` block called `disconnect()` in reverse order.

## Principles

- **A dependency is a class.** A subclass of `Client` with a type-hinted `__init__` and async
  `connect()` / `disconnect()` is the whole model: no providers, no modules, no registration, no
  scopes to configure. A third-party object becomes a dependency by wrapping it in such a class.
- **Type hints are the wiring.** A client asks for its dependencies in `__init__`, a function in
  its signature. Nothing else names them, so renaming or adding a dependency is an ordinary refactoring.
- **Concurrent startup, ordered shutdown.** Clients connect layer by layer, from the deepest
  dependencies up, and the clients of one layer connect concurrently. They disconnect in reverse,
  and a failing `disconnect()` does not stop the others.
- **Fail fast.** A tree that cannot be built fails before anything connects, naming the argument and
  the path to it. A client that cannot connect stops the application once the connected ones are
  disconnected. There are no retries: restarting is the orchestrator's job.
- **Tests replace, they do not rewire.** `mock()` and `override()` put a fake in place of a client
  for one test; the code under test does not change.
- **No runtime dependencies.** The core uses only the standard library; the framework
  integrations are extras.

## Performance

`benchmarks/compare.py` runs the same trees of clients through dishka, wireup, dependency-injector and
injector, each registering the same classes its own way: a cold container with the root resolved, on
classes new to the process, the root again, and one FastAPI request through each library's integration:

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

![nuke-di against other DI libraries: lower is better](https://raw.githubusercontent.com/troyan-dy/nuke-di/master/docs/benchmarks/compare.png)

So, is `nuke-di` the fastest? At building a tree with real type hints and at a FastAPI request, yes:
dependency-injector and injector take 2–2.5 times as long for the tree, dishka and wireup 24–37 times as
long, for validating the graph when the container is created, and wireup and dependency-injector twice as
long per request. With string annotations dependency-injector, which reads no annotation, is ahead by a fifth.
On a cached root `nuke-di` is level with wireup, and the Cython `get()` of dependency-injector wins by about
50 ns, a difference no application notices.

On its own, `resolve()` costs 4–7 µs per client, so a tree of 1000 clients is built in under 6 ms, and
`connect()` adds 9–15 µs per client in a layer. [docs/benchmarks.md](https://github.com/troyan-dy/nuke-di/blob/master/docs/benchmarks.md) explains every
scenario, records the baseline on Python 3.11–3.14 and has the whole comparison with its method.

## A job with command-line arguments

One decorator turns an async function into the main program of a process. Clients are injected, and every
other annotated argument becomes a command-line option, typed and validated:

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

No `main()`, no `asyncio.run()`, no `argparse`: the decorator resolves and connects the clients, parses
the command line, runs the function and exits with a meaningful code:

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

`--help` is generated from the signature and the docstring (Python 3.13+ prints `-d, --day DAY` instead of
`-d DAY, --day DAY`):

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

A wrong command line is rejected before any client is connected, with exit code `2`:

```console
$ python sync.py -d 2026-10-01 --mode full
usage: sync.py [-h] -d DAY [-t TABLES] [--mode {INCREMENTAL,FULL}]
               [--dry-run | --no-dry-run]
sync.py: error: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
Run sync.sync failed: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
$ echo $?
2
```

`@worker` does the same for a process that runs until SIGTERM, with a graceful shutdown. Both are
described in [Workers and jobs](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/workers-and-jobs.md).

## FastAPI

A path operation takes a client by its type hint, with no `Depends` and no `inject()` per handler.
`app/clients.py` holds the `Database` and `UserService` classes of the [Quick start](#quick-start), without its
`main()`:

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

The clients connect on startup and disconnect on shutdown, and a dependency such as `current_user` takes
clients the same way. Importing the app builds nothing, so a test replaces a client before `TestClient`
starts it:

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

Routers, websockets and the app's own lifespan are covered in [FastAPI](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/fastapi.md);
[Litestar](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/litestar.md) and [FastStream](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/faststream.md) work the same way.

## Documentation

- [Clients](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/clients.md): `Client` and `NotSingletonClient`, the lifecycle, dataclass clients,
  layers, startup timings, the dependency graph, connect and resolution errors
- [The container](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/container.md): `Dependencies` and the global `DI`, `resolve()`, `inject()`,
  `mock()`, `override()`
- [Workers and jobs](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/workers-and-jobs.md): `@job` and `@worker`, command-line parameters,
  `Shutdown`, the grace period, background tasks, exit codes, hooks, Kubernetes
- Frameworks: [FastAPI](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/fastapi.md), [Litestar](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/litestar.md),
  [FastStream](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/faststream.md)
- [Testing](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/testing.md): `mock()`, `override()`, the pytest fixtures, checking the wiring
- [Configuration](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/configuration.md): timeouts, concurrency and the grace period
- [Errors](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/errors.md): every exception and when it is raised
- [Examples](https://github.com/troyan-dy/nuke-di/blob/master/examples/README.md): 21 runnable scenarios, from a one-off script and a queue worker
  to FastAPI, Litestar, FastStream, Starlette and a whole service, each with its output and tests
- [Benchmarks](https://github.com/troyan-dy/nuke-di/blob/master/docs/benchmarks.md): every scenario, the baseline on Python 3.11–3.14 and the comparison
  with other libraries
- [Development](https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/development.md): the checks, coverage and releases

## License

[MIT](LICENSE)
