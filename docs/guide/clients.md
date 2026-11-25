# Clients

← [Documentation](../../README.md#documentation)

## Client

Every dependency subclasses `Client`. A container builds one instance per client class and shares it
with every consumer.

```python
from nuke_di import Client, Dependencies


class Settings(Client):
    pass


class Orders(Client):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class Payments(Client):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


deps = Dependencies()
orders = deps.resolve(Orders)
payments = deps.resolve(Payments)

print(orders.settings is payments.settings)
print(deps.resolve(Orders) is orders)
```

```text
True
True
```

A client declares its own dependencies as annotated `__init__` arguments. Only arguments
annotated with a client type are injected, and resolution is recursive.

A client lives as long as its container. For state that lives for one request or message, such as a
transaction, use a client method that opens it in the handler
([ADR-0006](../adr/0006-clients-live-as-long-as-the-container.md)).

## connect() and disconnect()

Override the async `connect()` / `disconnect()` methods to open and release resources such
as connection pools. `__init__` only stores the dependencies; anything that does I/O belongs
in `connect()`:

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

Each `connect()` is bounded by `CONNECT_TIMEOUT_SECONDS` (default `30`) and each
`disconnect()` by `DISCONNECT_TIMEOUT_SECONDS` (default `10`). A failing or hanging
`disconnect()` is logged, and the other clients still shut down. The whole disconnect, whatever
the depth of the tree, is bounded by `DISCONNECT_DEADLINE_SECONDS` (default `20`): a client
still disconnecting when it runs out is cut off, and the clients that have not started
disconnecting by then are skipped, with one log record that names them. See
[Lifecycle guarantees](lifecycle.md) for rollback, cancellation and integration behavior.

## Dataclass clients

Use `dataclasses.dataclass` on a `Client` subclass. The generated `__init__` declares its
fields as annotated arguments, which the container resolves in the same way as a handwritten
constructor.

```python
from dataclasses import dataclass

from nuke_di import Client, Dependencies


class Postgres(Client):
    pass


class Payments(Client):
    pass


@dataclass(frozen=True)
class Checkout(Client):
    pg: Postgres
    payments: Payments


checkout = Dependencies().resolve(Checkout)
print(checkout)
print(isinstance(checkout, Client))
```

```text
Checkout(pg=<__main__.Postgres object at 0x...>, payments=<__main__.Payments object at 0x...>)
True
```

## Connect order

A client connects as soon as its own dependencies have, concurrently with every other client
that is ready, so a slow client holds back only the clients that need it. `disconnect()` goes
the other way: a client disconnects as soon as the clients that depend on it have.

```python
# connect_order.py
import asyncio
import logging
import time

from nuke_di import Client, Dependencies

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
started = time.perf_counter()


async def connecting(name: str, seconds: float) -> None:
    await asyncio.sleep(seconds)  # a real client opens its connection here
    print(f"{time.perf_counter() - started:.2f}s  {name} connected")


class Postgres(Client):
    async def connect(self) -> None:
        await connecting("Postgres", 0.3)


class Kafka(Client):
    async def connect(self) -> None:
        await connecting("Kafka", 0.05)


class Redis(Client):
    async def connect(self) -> None:
        await connecting("Redis", 0.05)


class Repository(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Consumer(Client):
    def __init__(self, kafka: Kafka) -> None:
        self.kafka = kafka

    async def connect(self) -> None:
        await connecting("Consumer", 0.3)


class Http(Client):
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def connect(self) -> None:
        await connecting("Http", 0.2)


class App(Client):
    def __init__(self, repository: Repository, consumer: Consumer, http: Http) -> None:
        self.repository, self.consumer, self.http = repository, consumer, http


async def main() -> None:
    deps = Dependencies()
    deps.resolve(App)
    async with deps:
        print("-- application is running --")


asyncio.run(main())
```

```console
$ python connect_order.py
0.05s  Kafka connected
0.05s  Redis connected
0.25s  Http connected
0.30s  Postgres connected
0.35s  Consumer connected
INFO Connected 7 clients in 0.35s
-- application is running --
```

`Consumer` needs only `Kafka`, so it starts at 0.05s while `Postgres` is still connecting, and
the startup takes as long as its longest chain of dependencies, `Kafka` → `Consumer`. Up to
1.12 the clients connected in layers, each waiting for the slowest client of the layer below,
which took 0.60s here, and in 1.0 one after another in resolution order, which took 0.90s:

![The six clients of the example connected one after another in 0.90s, by layer in 0.60s and by their own dependencies in 0.35s](../connect-order.svg)

With `DEBUG` on, the `nuke_di` logger names every client as it starts and finishes, with how
many have connected so far: `Connecting client Consumer (2/7 connected)`, and the same for
`disconnect()`.

Only dependencies declared in `__init__` are ordered. If a client needs another one to be
connected first, declare it as a dependency.

## When a client fails to connect

If a client fails to connect, every client still connecting is cancelled and the clients
waiting for it never start. The clients that already connected are disconnected, each after
the clients that depend on it, and the container is left disconnected and empty:

```python
import asyncio

from nuke_di import Client, ConnectError, Dependencies


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")


class Kafka(Client):
    async def connect(self) -> None:
        raise OSError("broker kafka-1:9092 is unreachable")


class Orders(Client):
    def __init__(self, pg: Postgres, kafka: Kafka) -> None:
        self.pg, self.kafka = pg, kafka


async def main() -> None:
    deps = Dependencies()
    deps.resolve(Orders)
    try:
        await deps.connect()
    except ConnectError as exc:
        print(f"{exc} <- {exc.__cause__!r}")
    print("connected:", deps.connected)


asyncio.run(main())
```

```text
postgres: connected
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
postgres: disconnected
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable <- OSError('broker kafka-1:9092 is unreachable')
connected: False
```

The same cleanup happens when `connect()` itself is cancelled. The original failure is available
as the `__cause__` of `ConnectError`. Replacements do not take part in startup.

## When the tree cannot be built

Resolution checks every `__init__` before it calls it, so a client that cannot be built fails
before anything connects, with the argument named and the path from the client you asked for:

```python
from typing import Protocol

from nuke_di import Client, Dependencies, InvalidSignatureError


class Postgres(Client):
    pass


class UserRepository(Protocol):
    async def get(self, user_id: int) -> str: ...


class Profiles(Client):
    def __init__(self, pg: Postgres, users: UserRepository) -> None:
        self.pg, self.users = pg, users


class Checkout(Client):
    def __init__(self, profiles: Profiles) -> None:
        self.profiles = profiles


class Orders(Client):
    def __init__(self, payments: "Payments") -> None:
        self.payments = payments


class Payments(Client):
    def __init__(self, orders: Orders) -> None:
        self.orders = orders


for root in (Checkout, Orders):
    try:
        Dependencies().resolve(root)
    except InvalidSignatureError as exc:
        print(f"{type(exc).__name__}: {exc}")

try:
    Dependencies().resolve(UserRepository)  # a type checker refuses this line, and so does the container
except InvalidSignatureError as exc:
    print(f"{type(exc).__name__}: {exc}")
```

```text
InvalidSignatureError: Argument "users" of "Profiles.__init__" is UserRepository, which is not a client (resolving Checkout -> Profiles)
CircularDependencyError: Circular dependency: Orders -> Payments -> Orders
InvalidSignatureError: UserRepository is not a client: subclass Client
```

An argument of `__init__` is filled with a client when its type hint is a client. Ordinary
constructor arguments need a default, which the container leaves alone. For a function passed
to `inject()`, the caller supplies ordinary arguments. Both signatures require client arguments
to use a concrete client type and accept keywords. An optional client is rejected even if it
has a default of `None`.

| Invalid argument | `InvalidSignatureError` message |
|---|---|
| Missing type hint | `has no type hint` |
| Required constructor argument with a non-client type | `is UserRepository, which is not a client` |
| `Client \| None` | `is Postgres \| None, a client cannot be optional` |
| Positional-only client (`/`) | `is positional-only, a client is passed by keyword` |

A class that is not a client at all, asked for with `resolve()`, fails with `UserRepository is not a client: subclass Client` before anything is built.

Clients that depend on each other in a cycle fail with `CircularDependencyError`, a subclass of
`InvalidSignatureError`, and a type hint that cannot be evaluated, e.g. a class defined inside a
function or imported under `TYPE_CHECKING`, with an `InvalidSignatureError` that says so. When the
error comes from `inject()`, the path starts at the function:
`(resolving handler -> Checkout -> Profiles)`. In an [executable module](workers-and-jobs.md), these errors stop startup before anything connects.

## Checking the tree with mypy

`nuke_di.mypy` is a mypy plugin that finds these errors while mypy checks the types, before a
process or a test runs. Enable it in `pyproject.toml`:

```toml
[tool.mypy]
plugins = ["nuke_di.mypy"]
```

At every `resolve()`, `inject()`, `@entrypoint` the plugin walks the `__init__` of every
client the call would build, as the container does, and reports what the container would raise,
with the same message:

```python
# tree.py
from typing import Protocol, reveal_type

from nuke_di import DI, Client, entrypoint


class Postgres(Client):
    pass


class UserRepository(Protocol):
    async def get(self, user_id: int) -> str: ...


class Profiles(Client):
    def __init__(self, pg: Postgres, users: UserRepository) -> None:
        self.pg, self.users = pg, users


class Checkout(Client):
    def __init__(self, profiles: Profiles) -> None:
        self.profiles = profiles


class Orders(Client):
    def __init__(self, payments: "Payments") -> None:
        self.payments = payments


class Payments(Client):
    def __init__(self, orders: Orders) -> None:
        self.orders = orders


async def greet(user_id: int, pg: Postgres) -> str:
    return f"Hello, user-{user_id}!"


DI.resolve(Checkout)
reveal_type(DI.inject(greet))


@entrypoint
async def settle(orders: Orders) -> None:
    pass
```

```console
$ mypy tree.py
tree.py:39: error: Argument "users" of "Profiles.__init__" is UserRepository, which is not a client (resolving Checkout -> Profiles)  [nuke-di]
tree.py:40: note: Revealed type is "def (user_id: int) -> typing.Coroutine[Any, Any, str]"
tree.py:43: error: Circular dependency: settle -> Orders -> Payments -> Orders  [nuke-di]
Found 2 errors in 1 file (checked 1 source file)
```

- Every row of the table above is checked, cycles too, and so is an argument without a type hint
  in the function given to `inject()`, `@entrypoint`. An error is reported at the call that
  would raise it, with the path from that call; a tree with several errors reports them all, where
  the container stops at the first.
- `inject()` returns the function without its client arguments, the type of the `partial` it
  builds: `def (user_id: int) -> Coroutine[Any, Any, str]` above, instead of
  `Callable[..., Coroutine[Any, Any, str]]`. An argument after a client one becomes keyword-only,
  since a positional value would land on the client's place.
- Left to the container: a type hint that cannot be evaluated at runtime, which mypy evaluates
  anyway; a class in a variable of type `type[...]`, which may hold a subclass with another
  `__init__`; a decorated or overloaded `__init__`; `inject()` of a class; the routes and handlers
  of the FastAPI, Litestar, FastStream and aiogram integrations; a type mypy does not know, such as a class
  of a library without type hints.
- An intended error, in a test of that error, is silenced with `# type: ignore[nuke-di]`.
- It works with mypy 1.13 and later, with the cache as without it: a change to a client deep in a
  tree checks the calls of that tree again. The mypy daemon, `dmypy`, may miss such a change until
  it restarts.
- Pyright has no plugin API. With Pyright, [a test that injects every entrypoint](testing.md)
  finds the same errors.
