# Executable modules

← [Documentation](../../README.md#documentation)

## The entrypoint decorator

Put `@entrypoint` on an async function at the end of an executable module.

```python
# app/clients.py
from nuke_di import Client


class Reports(Client):
    async def publish(self, day: str = "today") -> None:
        print(f"Report published for {day}")
```

```python
# app/report.py
from nuke_di import entrypoint

from app.clients import Reports


@entrypoint
async def main(reports: Reports) -> None:
    await reports.publish()
```

Run it with `python -m app.report`. A standalone file can also be run as `python report.py`.
The decorator resolves client parameters through the global `DI`, enters `async with DI` and
awaits the function. A normal import returns the original function and starts no clients.
Use one decorated function per executable module and define it after the code it depends on.

The command takes no arguments. A function can accept injected clients and use ordinary
Python defaults for its other parameters. Exceptions and process exit follow Python's rules. The
function's return value is ignored. Python exits after the decorator finishes.

## An explicit main

Use an explicit `main()` if the executable needs command-line arguments, task supervision or
its own shutdown policy. Parse arguments in the application, then pass values to the code that
uses the container.

```python
import argparse
import asyncio

from nuke_di import Dependencies

from app.clients import Reports


async def main(day: str) -> None:
    deps = Dependencies()
    reports = deps.resolve(Reports)
    async with deps:
        await reports.publish(day)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("day")
    asyncio.run(main(parser.parse_args().day))
```

An application that starts background tasks owns their completion or cancellation. Join them
inside the container's context so they can still use the clients during cleanup. For framework
processes, use the framework's own command and lifespan. See
[servers with an explicit lifespan](servers-in-workers.md) for the composition pattern.

## Migrating from 2.x

| 2.x API | 3.0 equivalent |
|---|---|
| `@job` or `@worker` with only injected arguments | `@entrypoint` for a module executable |
| `Option`, CLI parameters and return-code policies | Application argument parsing and an explicit `main()` |
| `Shutdown`, `BackgroundTasks`, process hooks and grace period | Application or framework lifecycle and task ownership |
| `DI.graph()`, `Graph` and `Node` | A [wiring test](testing.md) and the dependency path in errors |
| `deps.timings` and `ClientTiming` | Container lifecycle logs |
| `client_dataclass` | `@dataclass` on a `Client` subclass |
| `CONNECT_CONCURRENCY` | Clients start as their dependencies become ready |
| `nuke_di.integration` | A supported integration or the framework's own lifespan around `async with deps` |
| Catching `SystemExit` for startup failures | Catch `ConnectError` or `InitializeDependencyError` directly |
| `Dependencies(clients=..., connect_clients=...)` | `Dependencies(settings=None)` and explicit `resolve()` or `mock()` calls before startup |
| `copy.deepcopy(deps)` | Create a fresh container and resolve the clients it needs. |
| `deps.connect_clients` or writes to `deps.clients` | Read `deps.clients` for a snapshot. Register test Replacements with `mock()` or `override()`. |
| `deps.clients[Database]` to retrieve a connected client | `deps.resolve(Database)` returns the typed client if startup already included it. |
| Reusing an injected callable across disconnects | Call `deps.inject(handler)` for each new lifecycle. |
| Optional or positional-only client parameters | Use a concrete client type that can be passed by keyword. |
| Setting timeout environment variables before importing `DI` | Set them before the first connect. See [Configuration](configuration.md). |
