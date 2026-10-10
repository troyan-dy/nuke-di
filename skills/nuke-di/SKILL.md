---
name: nuke-di
description: Use when writing, changing, reviewing or testing Python code that uses nuke-di (import nuke_di) - Client classes, DI.resolve / DI.inject, @job, @worker, nuke_di.fastapi / litestar / faststream / mcp / fastmcp, mock() / override(), the di / global_di pytest fixtures. Gives the whole model and the recipes, and keeps out the designs nuke-di rejects - provider functions, interface binding, per-request scopes and retries in connect().
---

# nuke-di

nuke-di is a small dependency injection library for async Python. Its whole model fits on this
page, and it deliberately lacks features other DI libraries have. Write code in its shape, not in
the shape of dishka, dependency-injector, injector, wireup or FastAPI `Depends`.

Full manual in one file: https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt

## The model

- **A dependency is a class**: a subclass of `Client` whose `__init__` takes its own dependencies
  as type-hinted arguments. The type hint is the only registration; there is no container setup.
- **`__init__` only stores**; I/O goes into `async def connect()`, cleanup into
  `async def disconnect()`. Both are optional.
- **One instance per container**: a `Client` lives as long as its container. `DI` is the global
  container; `Dependencies()` is a fresh one.
- **Lifecycle**: build the tree (`DI.resolve(cls)`, `DI.inject(func)`, `@job`, `@worker` or a
  framework `setup()`), then `async with DI:` connects every client, dependencies first, and
  disconnects them in reverse on exit. Nothing is resolved while the container is connected.
- **A function takes clients by type hint** too: `DI.inject(handler)` binds every argument whose
  type hint is a client; the other arguments stay for the caller.

```python
from nuke_di import DI, Client


class Settings(Client):  # configuration is a client too, see below
    database_url = "postgresql://localhost/app"


class Database(Client):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: Pool | None = None

    async def connect(self) -> None:
        self._pool = await create_pool(self._settings.database_url)

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    async def fetch_user(self, user_id: int) -> str: ...


class UserService(Client):
    def __init__(self, db: Database) -> None:
        self._db = db

    async def greet(self, user_id: int) -> str:
        return f"Hello, {await self._db.fetch_user(user_id)}!"


async def handler(user_id: int, users: UserService) -> str: ...


async def main() -> None:
    injected = DI.inject(handler)  # builds UserService -> Database -> Settings
    async with DI:  # connect() all, disconnect() all on exit
        await injected(42)
```

## Recipes

**A third-party object** (an `httpx.AsyncClient`, an asyncpg pool, a Kafka producer) becomes a
`Client` subclass that creates the object in `connect()`, closes it in `disconnect()` and exposes
the methods the application needs. There is no other way to register it.

**Configuration** is a client too, read from the environment when the container builds it:

```python
import os
from dataclasses import field

from nuke_di import Client, client_dataclass


@client_dataclass(frozen=True)
class Settings(Client):  # keep the Client base: it is what the type checker sees
    database_url: str = field(default_factory=lambda: os.environ["DATABASE_URL"])
```

`@client_dataclass` turns the fields into injected dependencies, like `__init__` arguments.

**Two instances of one kind** (a primary and a replica) are two classes: `class Replica(Postgres)`
with its own settings. **Several plugins** are a client that takes each of them in `__init__`.
**An implementation per environment** is a client that picks one from its settings in `connect()`
and delegates to it.

**A process**: one decorated async function per module, defined last. It parses its other
annotated arguments from the command line (`Annotated[T, Option(help=..., short=...)]`), connects
the clients, runs and exits with a meaningful code. Run it with `python -m app.jobs.sync`.

```python
from nuke_di import Shutdown, job, worker


@job  # runs once
async def sync(pg: Postgres, day: datetime.date) -> None: ...


@worker  # runs until SIGTERM / SIGINT
async def consume(queue: Queue, shutdown: Shutdown) -> None:
    while not shutdown.is_set():
        await handle(await queue.get())
```

**Frameworks**: handlers take clients by type hint, with no `Depends` / `Provide` / `inject()`
per handler.

- FastAPI: `from nuke_di.fastapi import setup`; `setup(app)` right after `app = FastAPI()`, before the routes.
- Litestar: `from nuke_di.litestar import ClientPlugin`; `Litestar(handlers, plugins=[ClientPlugin()])`.
- FastStream: `from nuke_di.faststream import setup`; `setup(app)` after `app = FastStream(broker)`.
- MCP SDK (`mcp` 2.x): `from nuke_di.mcp import setup`; `setup(server)` right after
  `server = MCPServer(...)`, before the tools. Tools and their `Resolve(...)` resolvers take clients;
  resources and prompts do not.
- FastMCP: `from nuke_di.fastmcp import setup`; `setup(mcp)` right after `mcp = FastMCP(...)`, before the
  tools, resources and prompts, which take clients, as do their `Depends(...)` functions.

**Per-request state** (a transaction, a unit of work, a request id) is not a client: open it in
the handler through a method of a long-lived client, e.g. `async with db.transaction() as tx:`.

**Tests** replace a client; they never rewire the code under test. Replace before anything is
resolved:

```python
from nuke_di import DI, Dependencies


async def test_greet(di: Dependencies) -> None:  # `di`: a fresh container, from the pytest plugin
    di.mock(Database).fetch_user.return_value = "alice"  # an autospec AsyncMock
    users = di.resolve(UserService)
    async with di:
        assert await users.greet(1) == "Hello, alice!"


def test_api() -> None:  # the global DI, e.g. a FastAPI app; a Replacement is never connected
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/users/1").json() == "Hello, alice!"
```

- `di` gives a fresh `Dependencies`, `global_di` the global `DI` flushed around the test. Async
  tests need pytest-asyncio (`asyncio_mode = auto`).
- A job or a handler can also be called directly with `AsyncMock()` arguments: importing the
  module does not run it.

## Do not write

Each of these was proposed and rejected; the container has no API for them and will not get one.

| Do not write | Write instead | Why |
|---|---|---|
| Provider or factory functions, `@provide`, generator providers, registering an instance or a lambda | A `Client` subclass that creates the object in `connect()` | [ADR-0005](https://github.com/troyan-dy/nuke-di/blob/master/docs/adr/0005-third-party-objects-as-client-classes.md) |
| `bind(Protocol, Impl)`, an `__init__` argument typed with a `Protocol` / ABC, qualifiers (`Annotated[Postgres, "replica"]`), `list[Plugin]` multibinding | Depend on the concrete client; a subclass per instance; `mock()` / `override()` in tests | [ADR-0008](https://github.com/troyan-dy/nuke-di/blob/master/docs/adr/0008-clients-depend-on-concrete-clients.md) |
| Request / session / message scopes, a client per request, new code on `NotSingletonClient` | A long-lived `Client` with a method that opens the per-request object | [ADR-0006](https://github.com/troyan-dy/nuke-di/blob/master/docs/adr/0006-clients-live-as-long-as-the-container.md) |
| Retries, backoff or "wait until ready" loops in `connect()` | Let `connect()` raise; the orchestrator restarts the process | [ADR-0009](https://github.com/troyan-dy/nuke-di/blob/master/docs/adr/0009-connect-is-fail-fast.md) |
| I/O in `__init__`, or `DI.resolve()` inside a handler or while connected | `__init__` stores, `connect()` does I/O; resolve at startup | The tree is built before anything connects |
| FastAPI `Depends(get_db)` or `DI.inject` per route to reach a client | A route argument typed with the client, after `setup(app)` | The integration does it |
| `DI.resolve(...)` plus `asyncio.run()` boilerplate around a process | `@job` / `@worker` | They own the lifecycle and the exit code |

## Check the result

- **mypy plugin**: with `plugins = ["nuke_di.mypy"]` under `[tool.mypy]`, `mypy` reports an
  argument that is not a client, a missing type hint and a cycle at every `resolve()`, `inject()`,
  `@job` and `@worker`, with the path to it.
- **A wiring test** builds every entrypoint without connecting anything; it also runs every
  `__init__`:

  ```python
  @pytest.mark.parametrize("entrypoint", [sync.sync, consumer.consume])
  def test_entrypoint_resolves(entrypoint: Callable[..., object]) -> None:
      Dependencies().inject(entrypoint)
  ```

- **The graph as data**: `deps.graph().nodes` lists every client a container built, dependencies
  first; `node.cls` is the class and `node.dependencies` maps each `__init__` argument to its
  node. Answer "does `OrderService` depend on `Database`" from it, or print
  `deps.graph().to_mermaid()`. A JSON form is a recipe in the guide:
  https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/agents.md#the-graph-as-json

## Errors

- `InvalidSignatureError ... which is not a client`: an argument of a client's `__init__` is typed
  with something that is not a client (a `Protocol`, an ABC, `str`). Make it a concrete `Client`,
  read the value from a settings client, or give the argument a default, which the container leaves
  alone. A function given to `inject()` keeps its non-client arguments for the caller instead.
- `CircularDependencyError`: two clients need each other; extract the shared part into a third
  client both depend on.
- `ConnectError: ... already resolved, call mock() before resolve() or inject()`: move the
  `mock()` / `override()` above the first `resolve()` / `inject()` / `TestClient(app)`.
- `ConnectError: ... the container is already connected`: resolve before `async with DI`.

Every exception: https://github.com/troyan-dy/nuke-di/blob/master/docs/guide/errors.md
