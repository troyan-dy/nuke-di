# The container

← [Documentation](../../README.md#documentation)

`Dependencies(settings=None)` creates a container. `DI` provides a shared global container.
Each container compares by identity, is hashable, and shows its state, clients and Replacements
in `repr()`. The read-only `clients` mapping is a snapshot from registered client classes to
their instances. Create a fresh container when a separate lifecycle is needed.

| Method | Description |
|---|---|
| `resolve(client_class)` | Return the typed instance already held by the container. Build a new instance and its dependencies while disconnected. |
| `inject(func)` | Build the clients of `func` and return a callable with those arguments bound. |
| `connect()` | Connect every resolved client after its dependencies. |
| `disconnect()` | Disconnect each client after its consumers and clear the resolved clients. |
| `async with` | Connect on entry and disconnect on exit. |
| `mock(client_class, new=None)` | Register a Replacement before the client is resolved. It lasts until the next flush. |
| `override(client_class, new=None)` | Register a Replacement for a `with` block. See [Testing](testing.md). |
| `flush()` | Forget resolved clients while disconnected. Active overrides retain their Replacements. |

Resolve clients and bind callables before startup. During startup and while connected, `resolve()`
can return an existing client or Replacement, preserving its type. Asking for a new client in
either state raises `RuntimeError`. Wait for startup to finish before using a client's resources.
`inject()`, `mock()`, `override()` and `flush()` require a disconnected container.
While disconnecting, `connected` remains true until cleanup clears the registry, and every
`resolve()` call raises `RuntimeError`.
See [Lifecycle guarantees](lifecycle.md) for failures and cancellation.

```python
from nuke_di import Client, Dependencies


class Database(Client):
    def fetch(self, user_id: int) -> str:
        return f"user-{user_id}"


async def handler(user_id: int, db: Database) -> str:
    return db.fetch(user_id)


async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # builds Database
    async with deps:
        db = deps.resolve(Database)  # returns the same Database, with its type
        assert db is deps.clients[Database]
        assert await injected(42) == "user-42"
```

An injected callable preserves the function's return type and whether an async function must
be awaited. The mypy plugin also checks the remaining parameters. Each regular parameter needs
a type hint, and the caller still supplies non-client arguments. Client parameters must use a
concrete client type and accept keyword arguments. See [signature errors](clients.md#when-the-tree-cannot-be-built).

When `flush()` or `disconnect()` clears the resolved clients, a callable that holds any of
those clients raises `RuntimeError` before it runs. A coroutine checks this when awaited,
and a generator checks when iteration starts. Bind the function again with `inject()` before reconnecting. The callable can still be used
before the first connect, for example with test Replacements.

The container is safe to resolve from several threads: one lock per container serializes `resolve`,
`inject`, `mock`, `override` and `flush`, so a singleton asked for by two threads at once is built once.
`connect()` and `disconnect()` belong to one event loop.

```python
import threading

from nuke_di import Client, Dependencies


class Postgres(Client):
    instances = 0

    def __init__(self) -> None:
        type(self).instances += 1


class Orders(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


deps = Dependencies()
threads = [threading.Thread(target=deps.resolve, args=(Orders,)) for _ in range(8)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
print("instances:", Postgres.instances, "clients:", len(deps.clients))
```

```text
instances: 1 clients: 2
```
