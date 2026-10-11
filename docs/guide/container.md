# The container

← [Documentation](../../README.md#documentation)

`Dependencies` is the container. `DI` is a ready-to-use global instance; create your own
when you need isolation, e.g. in tests.

| Method               | Description                                                             |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Build `cls` and its dependency tree. Idempotent for `Client`.           |
| `inject(func)`       | Return `functools.partial(func, ...)` with client arguments bound. Every argument of `func` except `*args` / `**kwargs` must have a type hint. |
| `connect()`          | Call `connect()` on every resolved client, each after its dependencies. |
| `disconnect()`       | Call `disconnect()` on every client, each after its consumers, then `flush()` the container. |
| `async with`         | `connect()` on enter, `disconnect()` on exit.                           |
| `mock(cls, new=None)`| Register a Replacement for `cls` (an autospec mock by default) until the next `flush()`. Must come before `cls` is resolved. |
| `override(cls, new=None)` | A Replacement for the duration of a `with` block, then `flush()`; see [Testing](testing.md). |
| `flush()`            | Forget every resolved client.                                           |
| `timings`            | One `ClientTiming` per client of the last `connect()`; see [Startup timings](clients.md#startup-timings). |
| `graph()`            | A `Graph` of the resolved clients with their dependencies, `to_mermaid()` included; see [The graph](clients.md#the-graph). |

The result of `inject()` keeps the return type of the function, while its remaining arguments are
untyped: a type checker cannot subtract the client arguments from a signature.

`resolve`, `inject`, `mock`, `override` and `flush` only work while the container is disconnected:
the whole tree is built before startup.

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: resolve(Cache): the container is already connected; ...
```

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
print("instances:", Postgres.instances, "clients:", len(deps.connect_clients))
```

```text
instances: 1 clients: 2
```
