# taskiq

**English** · [Русский](../i18n/ru/taskiq.md) · [简体中文](../i18n/zh-CN/taskiq.md) · [Español](../i18n/es/taskiq.md) · [Português (Brasil)](../i18n/pt-BR/taskiq.md) · [日本語](../i18n/ja/taskiq.md) · [Polski](../i18n/pl/taskiq.md)

← [Documentation](../../README.md#documentation)

A taskiq task takes a client by its type hint, next to the arguments it is kicked with:

```bash
pip install "nuke-di[taskiq]"
```

Requires taskiq 0.11 or newer, with any broker. With the clients of the [FastAPI](fastapi.md) examples, and an
`InMemoryBroker`, which runs the tasks in the process that kicks them:

```python
# app/tasks.py
from typing import Annotated

from taskiq import Context, InMemoryBroker, TaskiqDepends

from app.clients import Database, UserService
from nuke_di.taskiq import setup

broker = InMemoryBroker()  # runs the tasks in this process
setup(broker)  # clients connect when the worker starts, disconnect when it stops


@broker.task
async def send_report(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))


async def account_name(context: Annotated[Context, TaskiqDepends()], db: Database) -> str:
    # A dependency gets taskiq's own objects and clients side by side
    return await db.fetch_user(context.message.kwargs["account_id"])


@broker.task
async def close_account(account_id: int, name: Annotated[str, TaskiqDepends(account_name)]) -> str:
    return f"closed the account of {name}"
```

```python
# app/main.py
import asyncio

from app.tasks import broker, close_account, send_report


async def main() -> None:
    # An InMemoryBroker is its own worker: its startup connects the clients
    await broker.startup()
    report = await send_report.kiq(42)
    await report.wait_result()
    closed = await close_account.kiq(account_id=7)
    print((await closed.wait_result()).return_value)
    await broker.shutdown()


asyncio.run(main())
```

```console
$ python -m app.main
database: connected
Hello, user-42!
closed the account of user-7
database: disconnected
```

**Type checkers.** taskiq types `.kiq()` with the task's own signature, so mypy and pyright ask for `users` in
`send_report.kiq(42)`; the same goes for every argument taskiq fills, `Annotated` or not. A default makes the
argument optional for them, and the client still comes from the container by its type:

```python
@broker.task
async def send_report(user_id: int, users: UserService = TaskiqDepends()) -> None:
    print(await users.greet(user_id))
```

Ruff's `B008` flags a call in a default; taskiq's markers are safe there:

```toml
# pyproject.toml
[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["taskiq.TaskiqDepends"]
```

**A real worker.** In a deployment the broker is that of a queue, e.g. `NatsBroker` of
[taskiq-nats](https://github.com/taskiq-python/taskiq-nats); nothing else changes:

```python
# app/tasks.py
import os

from taskiq_nats import NatsBroker

from app.clients import UserService
from nuke_di.taskiq import setup

broker = NatsBroker(os.environ.get("NATS_URL", "nats://localhost:4222"), queue="reports")
setup(broker)


@broker.task
async def send_report(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))
```

The worker process connects the clients; a process that only kicks tasks, such as a web app, connects the
broker and nothing else:

```python
# app/kick.py
import asyncio

from app.tasks import broker, send_report


async def main() -> None:
    # A client process: the broker connects to NATS, the clients stay unconnected
    await broker.startup()
    await send_report.kiq(42)
    print("kicked send_report(42)")
    await broker.shutdown()


asyncio.run(main())
```

```console
$ taskiq worker app.tasks:broker --workers 1
[2026-10-10 18:40:00,853][taskiq.worker][INFO   ][MainProcess] Pid of a main process: 56250
[2026-10-10 18:40:00,853][taskiq.worker][INFO   ][MainProcess] Starting 1 worker processes.
[2026-10-10 18:40:00,859][taskiq.process-manager][INFO   ][MainProcess] Started process worker-0 with pid 56252
database: connected
[2026-10-10 18:40:01,084][nuke_di.core][INFO   ][worker-0] Connected 2 clients in 0.00s (slowest: Database 0.00s, UserService 0.00s)
[2026-10-10 18:40:01,091][taskiq.receiver.receiver][INFO   ][worker-0] Listening started.
[2026-10-10 18:40:03,815][taskiq.receiver.receiver][INFO   ][worker-0] Executing task app.tasks:send_report with ID: e987ddad37444fa5a0ade0174578c9f2
Hello, user-42!
^C
[2026-10-10 18:40:05,845][taskiq.process-manager][INFO   ][MainProcess] Workers are scheduled for shutdown.
[2026-10-10 18:40:05,995][taskiq.process-manager][INFO   ][MainProcess] Stopped process worker-0 with pid 56252
[2026-10-10 18:42:01,140][taskiq.receiver.receiver][INFO   ][worker-0] Stopping prefetching messages...
[2026-10-10 18:42:01,143][taskiq.receiver.receiver][INFO   ][worker-0] The runner is stopped.
[2026-10-10 18:42:01,144][taskiq.worker][INFO   ][worker-0] Shutting down the broker.
database: disconnected
```

In another terminal:

```console
$ python -m app.kick
kicked send_report(42)
```

The two minutes before `Stopping prefetching messages...` are taskiq's: its worker process (taskiq 0.13 with
taskiq-nats 0.7) notices the signal at its next message or NATS ping.

The rules:

- **Where clients are filled.** In the arguments of the broker's tasks and of every `TaskiqDepends(...)`
  function they use, at any depth, generator dependencies included. Every other argument is taskiq's: the
  arguments of `.kiq()`, `Context`, `TaskiqState`. A dependency class, `Annotated[Auth, TaskiqDepends()]`, is
  built by taskiq from its own `__init__`, which nuke-di does not rewrite, so one whose `__init__` takes
  clients is refused when its task is registered: `TypeError: Auth takes clients in __init__ and is a taskiq
  dependency`. Take a client by its type hint, without `TaskiqDepends()`, and a class that needs clients
  through a dependency function.
- **Which clients start.** Those of every task in `broker.get_all_tasks()`: the broker's own and the shared
  ones (`@shared_task`). Tasks of the broker may be declared before or after `setup(broker)`. A shared task is
  registered on taskiq's shared broker, which `setup()` does not hook: `setup()` rewrites the shared tasks
  there are, and the worker's startup the rest. `taskiq worker` reads the signatures of its tasks before it
  starts, so there a shared task is imported before `setup()`; an `InMemoryBroker` reads one on its first run,
  so there either order works.
- **Which process connects.** The one where the broker fires `WORKER_STARTUP`: a `taskiq worker` process, and
  any process that starts an `InMemoryBroker`, which is its own worker. A process that only kicks tasks
  starts the broker with `CLIENT_STARTUP` and connects no client. So one module with the broker serves both:
  the worker connects through taskiq, the web app through its own integration.
- **Lifespan.** The clients connect before the other `WORKER_STARTUP` handlers, including those registered
  before `setup()`, and disconnect after `broker.shutdown()` has run the `WORKER_SHUTDOWN` handlers, the
  middlewares and the result backend; on an `InMemoryBroker`, after the tasks still running have finished.
  `Shutdown` and `BackgroundTasks` behave as in [FastAPI](fastapi.md). `taskiq worker` gives
  `broker.shutdown()` `--shutdown-timeout` seconds, 5 by default, and each `disconnect()` may take
  `DISCONNECT_TIMEOUT_SECONDS`, 10 by default: set `--shutdown-timeout` above the longest chain of
  disconnects, or a slow one is cut off midway.
- **A failed start.** A failed `connect()` fails `broker.startup()` with a `RuntimeError`, and the worker
  process dies. taskiq's process manager restarts it for ever by default (`--max-fails -1`), so the
  orchestrator never sees a crash and the dead dependency is tried again every second. Run the worker with
  `--max-fails 1`: it then exits with code 255, and the orchestrator restarts it with its own backoff, as
  `connect()` is fail-fast in nuke-di.
- **Instances.** As with `inject()`, a `Client` is one instance per container, and a `NotSingletonClient` is
  one instance per argument that declares it, not one per task.
- **The function stays a function.** Its signature shows `Annotated[UserService, TaskiqDepends(...)]` to
  taskiq, as in [FastAPI](fastapi.md); `await send_report(1, users)` calls it with clients passed by hand.
  A function that takes clients serves either taskiq or FastAPI: one bound to FastAPI is refused with a
  `TypeError` before its task is registered.
- **One container connects once.** An `InMemoryBroker` started in a process whose container is already
  connected, e.g. inside a FastAPI app that runs on the same `DI`, fails with `RuntimeError: nuke-di clients
  failed to start: the container is already connected`. Give that broker a container of its own:
  `setup(broker, container=Dependencies())`. For the same reason a worker fails to start with
  `taskiq_fastapi.init(broker, app)` for an app set up with `nuke_di.fastapi` on the same container: it enters
  the app's lifespan on `WORKER_STARTUP`. With `nuke_di.taskiq` the tasks get their clients without it. A task
  function serves one broker at a time, as a FastStream subscriber does.

**Testing.** Run the tasks on an `InMemoryBroker` and start it inside the override:

```python
# tests/test_tasks.py
import pytest

from app.clients import Database
from app.tasks import broker, send_report
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_send_report(capsys: pytest.CaptureFixture[str]) -> None:
    with DI.override(Database, FakeDatabase()):
        await broker.startup()
        try:
            task = await send_report.kiq(1)
            await task.wait_result()
        finally:
            await broker.shutdown()

    assert "Hello, alice!" in capsys.readouterr().out
```

```console
$ pytest -q tests/test_tasks.py
.                                                                        [100%]
1 passed in 0.44s
```

A task run without the worker's startup, e.g. kicked on an `InMemoryBroker` that was never started, fails
with ``RuntimeError: UserService is not connected: the clients connect when the worker starts; run tasks with
`taskiq worker`, or start an InMemoryBroker with `await broker.startup()` before kicking them`` in its result. A
task declared after the worker started fails with `RuntimeError: UserService was not started with the worker`.
