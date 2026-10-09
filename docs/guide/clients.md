# Clients

**English** · [Русский](../i18n/ru/clients.md) · [简体中文](../i18n/zh-CN/clients.md) · [Español](../i18n/es/clients.md) · [Português (Brasil)](../i18n/pt-BR/clients.md) · [日本語](../i18n/ja/clients.md) · [Polski](../i18n/pl/clients.md)

← [Documentation](../../README.md#documentation)

## Client and NotSingletonClient

Every dependency is a subclass of one of two base classes:

| Base class           | Instances                                          |
|----------------------|----------------------------------------------------|
| `Client`             | Singleton: one instance per container              |
| `NotSingletonClient` | A new instance for every consumer that declares it |

```python
from nuke_di import Client, Dependencies, NotSingletonClient


class Settings(Client):
    pass


class HttpSession(NotSingletonClient):
    pass


class Orders(Client):
    def __init__(self, settings: Settings, http: HttpSession) -> None:
        self.settings = settings
        self.http = http


class Payments(Client):
    def __init__(self, settings: Settings, http: HttpSession) -> None:
        self.settings = settings
        self.http = http


deps = Dependencies()
orders = deps.resolve(Orders)
payments = deps.resolve(Payments)

print(orders.settings is payments.settings)  # one Settings for the whole container
print(orders.http is payments.http)  # every consumer gets its own HttpSession
print(deps.resolve(Orders) is orders)  # resolve() is idempotent for a Client
```

```text
True
False
True
```

A client declares its own dependencies as annotated `__init__` arguments. Only arguments
annotated with a client type are injected, and resolution is recursive.

A client lives as long as its container. There are no per-request or per-message clients, and none
are planned ([ADR-0006](../adr/0006-clients-live-as-long-as-the-container.md)): a transaction or anything else that lives for one request is
opened in the handler through a method of a client. `NotSingletonClient` is still supported, but it
is slated for removal in a future major version, so do not build new code on it.

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
`disconnect()` is logged, and the other clients still shut down.

## Dataclass clients

`client_dataclass` turns a class into a `Client` and a dataclass at once, so the fields
become the injected dependencies. Subclass `Client` as well: the decorator is typed as an
identity, so the base class is what tells mypy and pyright that `Checkout` is a client; without
it the class is a client at runtime only:

```python
from nuke_di import Client, Dependencies, client_dataclass


class Postgres(Client):
    pass


class Payments(Client):
    pass


@client_dataclass(frozen=True)
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

It accepts the same keyword arguments as `dataclasses.dataclass`.

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
INFO Connected 7 clients in 0.35s (slowest: Postgres 0.30s, Consumer 0.30s, Http 0.20s)
-- application is running --
```

`Consumer` needs only `Kafka`, so it starts at 0.05s while `Postgres` is still connecting, and
the startup takes as long as its longest chain of dependencies, `Kafka` → `Consumer`. Up to
1.12 the clients connected in layers, each waiting for the slowest client of the layer below,
which took 0.60s here:

![The six clients of the example connected by layer in 0.60s and by their own dependencies in 0.35s](../connect-order.svg)

With `DEBUG` on, the `nuke_di` logger names every client as it starts and finishes, with how
many have connected so far: `Connecting client Consumer (2/7 connected)`, and the same for
`disconnect()`.

Only dependencies declared in `__init__` are ordered. If a client needs another one to be
connected first, declare it as a dependency. Set `CONNECT_CONCURRENCY` to limit how many
clients connect at once; a client waiting for its dependencies does not take a slot.

## Startup timings

The container measures every client's `connect()` and `disconnect()`, so a slow startup
names its culprit. After a successful `connect()` it logs a summary at `INFO`, and a
`WARNING` for every client that used more than half of `CONNECT_TIMEOUT_SECONDS`, long
before that client starts failing with a timeout:

```python
# startup.py
import asyncio
import logging

from nuke_di import Client, Dependencies, DependenciesSettings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.2)


class Kafka(Client):
    async def connect(self) -> None:
        await asyncio.sleep(1.6)

    async def disconnect(self) -> None:
        await asyncio.sleep(0.3)


class Orders(Client):
    def __init__(self, pg: Postgres, kafka: Kafka) -> None:
        self.pg, self.kafka = pg, kafka


async def main() -> None:
    deps = Dependencies(settings=DependenciesSettings(connect_timeout=3))
    deps.resolve(Orders)
    async with deps:
        print("-- application is running --")

    for t in deps.timings:
        print(
            f"{t.name:<8} connect {t.connect:.2f}s {t.connect_outcome:<3}  "
            f"disconnect {t.disconnect:.2f}s {t.disconnect_outcome}"
        )


asyncio.run(main())
```

```console
$ python startup.py
INFO Connected 3 clients in 1.60s (slowest: Kafka 1.60s, Postgres 0.20s, Orders 0.00s)
WARNING Client Kafka took 1.60s to connect, more than half of CONNECT_TIMEOUT_SECONDS (3s)
-- application is running --
Postgres connect 0.20s ok   disconnect 0.00s ok
Kafka    connect 1.60s ok   disconnect 0.30s ok
Orders   connect 0.00s ok   disconnect 0.00s ok
```

`deps.timings` holds one `ClientTiming` per client of the last `connect()`, in resolution
order, so a client comes after its dependencies. It outlives `disconnect()`, so it can be read
once the container has stopped. In a FastAPI app, the lifespan you pass to `FastAPI()` runs
inside the connected container, so it sees the connect timings. A worker or a job gets the same
list as [`Run.clients`](workers-and-jobs.md#startup-metrics-and-structured-logs).

| `ClientTiming` field | Value |
|----------------------|-------|
| `name`               | The class name of the client |
| `connect`            | Seconds spent in `connect()`, not counting the wait for `CONNECT_CONCURRENCY`; `None` if `connect()` never ran |
| `connect_outcome`    | `"ok"`, `"failed"`, `"timed_out"`, `"cancelled"`, or `None` if `connect()` never started |
| `disconnect`, `disconnect_outcome` | The same for `disconnect()`; `None` until the client disconnects |

When a client fails to connect, the clients still connecting end up `"cancelled"`, the
clients still waiting for their dependencies keep `None`, and the clients that had connected
are rolled back, so they get a `disconnect_outcome`. The library only measures: exporting the
timings as metrics or spans is up to your code.

## The graph

The dependency graph exists only inside a running process: the `DEBUG` log is the only place
that shows which clients an entrypoint pulls in and what each one waits for. `graph()` returns
the same picture as data, before `connect()` or after it:

```python
# graph.py
from nuke_di import Client, Dependencies


class Postgres(Client):
    pass


class Redis(Client):
    pass


class Payments(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Checkout(Client):
    def __init__(self, pg: Postgres, redis: Redis, payments: Payments) -> None:
        self.pg, self.redis, self.payments = pg, redis, payments


deps = Dependencies()
deps.resolve(Checkout)
nodes = {node.name: node for node in deps.graph().nodes}
for node in nodes.values():
    print(f"{node.name:<8} needs {list(node.dependencies)}")
print("shared:", nodes["Checkout"].dependencies["pg"] is nodes["Payments"].dependencies["pg"])
print(deps.graph().to_mermaid())
```

```console
$ python graph.py
Postgres needs []
Redis    needs []
Payments needs ['pg']
Checkout needs ['pg', 'redis', 'payments']
shared: True
graph BT
  Postgres
  Redis
  Payments
  Checkout
  Postgres --> Payments
  Postgres --> Checkout
  Redis --> Checkout
  Payments --> Checkout
```

GitHub renders the Mermaid text in a README, a pull request or an issue, so a project can show
its architecture without a running process:

```mermaid
graph BT
  Postgres
  Redis
  Payments
  Checkout
  Postgres --> Payments
  Postgres --> Checkout
  Redis --> Checkout
  Payments --> Checkout
```

`Graph.nodes` holds one `Node` per resolved client, in resolution order, so a client comes after
its dependencies. It is a snapshot: `flush()` empties it, apart from the Replacements of the open
`override()` blocks, which survive every `flush()`.

| `Node` field   | Value |
|----------------|-------|
| `name`         | The class name of the client |
| `cls`          | The class the consumers asked for |
| `singleton`    | `True` for a `Client`, `False` for a `NotSingletonClient` |
| `replacement`  | The object registered with `mock()` or `override()` in place of `cls`; `None` for a real client |
| `dependencies` | The clients of the `__init__` arguments, by argument name |

A `NotSingletonClient` gets one node per instance, all with the same name; `to_mermaid()` numbers
them from the second one (`Session`, `Session_2`). A Replacement is drawn with a
dashed border and the name of the object in its place: `Postgres: AsyncMock`. Nodes compare by
identity, so the `shared: True` above says that `Checkout` and `Payments` got the same `Postgres`.

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

The same cleanup happens when `connect()` itself is cancelled. `ConnectError` derives from
`SystemExit`, so an application that does not catch it stops, which is usually what you want
when a dependency is down. Mocked clients are not connected, and nothing waits for them.

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
InvalidSignatureError: UserRepository is not a client: subclass Client or NotSingletonClient
```

An argument of `__init__` is filled with a client when its type hint is a client. Any other
argument needs a default, which is left alone. These fail with `InvalidSignatureError`:

| `__init__` argument without a default | Message                                           |
|---------------------------------------|---------------------------------------------------|
| no type hint                          | `has no type hint`                                |
| a type that is not a client           | `is UserRepository, which is not a client`        |
| `Client \| None`                      | `is Postgres \| None, a client cannot be optional` |
| a client, positional-only (`/`)       | `is positional-only, a client is passed by keyword` |

A class that is not a client at all, asked for with `resolve()`, fails with `UserRepository is not a client: subclass Client or NotSingletonClient` before anything is built.

Clients that depend on each other in a cycle fail with `CircularDependencyError`, a subclass of
`InvalidSignatureError`, and a type hint that cannot be evaluated, e.g. a class defined inside a
function or imported under `TYPE_CHECKING`, with an `InvalidSignatureError` that says so. When the
error comes from `inject()`, the path starts at the function:
`(resolving handler -> Checkout -> Profiles)`. In a [worker or a job](workers-and-jobs.md)
each of these fails the run with exit code `1` before anything connects.

## Checking the tree with mypy

`nuke_di.mypy` is a mypy plugin that finds these errors while mypy checks the types, before a
process or a test runs. Enable it in `pyproject.toml`:

```toml
[tool.mypy]
plugins = ["nuke_di.mypy"]
```

At every `resolve()`, `inject()`, `@job` and `@worker` the plugin walks the `__init__` of every
client the call would build, as the container does, and reports what the container would raise,
with the same message:

```python
# tree.py
from typing import Protocol, reveal_type

from nuke_di import DI, Client, job


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


@job
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
  in the function given to `inject()`, `@job` or `@worker`. An error is reported at the call that
  would raise it, with the path from that call; a tree with several errors reports them all, where
  the container stops at the first.
- `inject()` returns the function without its client arguments, the type of the `partial` it
  builds: `def (user_id: int) -> Coroutine[Any, Any, str]` above, instead of
  `Callable[..., Coroutine[Any, Any, str]]`. An argument after a client one becomes keyword-only,
  since a positional value would land on the client's place.
- Left to the container: a type hint that cannot be evaluated at runtime, which mypy evaluates
  anyway; a class in a variable of type `type[...]`, which may hold a subclass with another
  `__init__`; a decorated or overloaded `__init__`; `inject()` of a class; the routes and handlers
  of the FastAPI, Litestar and FastStream integrations; a type mypy does not know, such as a class
  of a library without type hints.
- An intended error, in a test of that error, is silenced with `# type: ignore[nuke-di]`.
- It works with mypy 1.13 and later, with the cache as without it: a change to a client deep in a
  tree checks the calls of that tree again. The mypy daemon, `dmypy`, may miss such a change until
  it restarts.
- Pyright has no plugin API. With Pyright, [a test that injects every entrypoint](testing.md)
  finds the same errors.
