# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](#development)
[![License](https://img.shields.io/pypi/l/nuke-di)](LICENSE)

The simplest dependency injection for async Python projects.

Dependencies are declared with plain type hints. `nuke-di` builds the dependency tree,
creates every client once and drives its async lifecycle: `connect()` on startup and
`disconnect()` on shutdown, in reverse order.

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

### Container

`Dependencies` is the container. `DI` is a ready-to-use global instance; create your own
when you need isolation, e.g. in tests.

| Method               | Description                                                             |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Build `cls` and its dependency tree. Idempotent for `Client`.           |
| `inject(func)`       | Return `functools.partial(func, ...)` with client arguments bound. Every argument of `func` except `*args` / `**kwargs` must have a type hint. |
| `connect()`          | Call `connect()` on every resolved client, in resolution order.         |
| `disconnect()`       | Call `disconnect()` in reverse order, then `flush()` the container.     |
| `async with`         | `connect()` on enter, `disconnect()` on exit.                           |
| `mock(cls, new=None)`| Register a replacement for `cls` (an autospec mock by default).         |
| `flush()`            | Forget every resolved client.                                           |

`resolve`, `inject`, `mock` and `flush` only work while the container is disconnected:
the whole tree is built before startup.

A failing `disconnect()` is logged and does not stop the other clients from shutting down.

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

The value is read when a `Dependencies` instance is created. You can also pass it explicitly:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5))
```

## Errors

| Exception                   | Raised when                                               |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | A client's `__init__` raised                              |
| `ConnectError`              | A client's `connect()` raised, or the container state is wrong (e.g. resolving after connect) |
| `ConnectTimeoutError`       | A client's `connect()` exceeded `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError`     | `inject()` got a function with an argument without a type hint |

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
