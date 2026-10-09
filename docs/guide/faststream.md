# FastStream

**English** · [Русский](../i18n/ru/faststream.md) · [简体中文](../i18n/zh-CN/faststream.md) · [Español](../i18n/es/faststream.md) · [Português (Brasil)](../i18n/pt-BR/faststream.md) · [日本語](../i18n/ja/faststream.md) · [Polski](../i18n/pl/faststream.md)

← [Documentation](../../README.md#documentation)

A FastStream subscriber takes a client by its type hint, next to the message:

```bash
pip install "nuke-di[faststream]"
```

Requires FastStream 0.6 or newer, with any broker. With the clients of the [FastAPI](fastapi.md) examples:

```python
# app/worker.py
from faststream import FastStream
from faststream.nats import NatsBroker

from app.clients import UserService
from nuke_di.faststream import setup

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
setup(app)  # clients connect before the broker starts, disconnect after it stops


@broker.subscriber("greetings")
async def greet(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))
```

```console
$ faststream run app.worker:app
database: connected
2026-10-08 15:12:52,281 INFO     - FastStream app starting...
2026-10-08 15:12:52,287 INFO     - greetings |            - `Greet` waiting for messages
2026-10-08 15:12:52,287 INFO     - FastStream app started successfully! To exit, press CTRL+C
2026-10-08 15:12:55,078 INFO     - greetings | a747e4d0-2 - Received
Hello, user-42!
2026-10-08 15:12:55,079 INFO     - greetings | a747e4d0-2 - Processed
^C
2026-10-08 15:12:56,222 INFO     - FastStream app shutting down...
2026-10-08 15:12:56,223 INFO     - FastStream app shut down gracefully.
database: disconnected
```

The message was published with:

```python
# publish.py
import asyncio

from faststream.nats import NatsBroker


async def main() -> None:
    async with NatsBroker("nats://localhost:4222") as broker:
        await broker.publish(42, "greetings")


asyncio.run(main())
```

The rules:

- **Where clients are filled.** In the arguments of the subscribers of the app's brokers, those of
  included routers too, and of every `Depends(...)` they use, at any depth: functions and classes,
  including `dependencies=` of the subscriber, of its router and of the broker. Every other argument is
  FastStream's: the message, its fields, `Context()`.
- **Which clients start.** On startup, those of every subscriber the app's brokers serve, routers
  included. Subscribers may be declared before or after `setup(app)`.
- **Lifespan.** The clients connect before the app's own `lifespan=` and `on_startup=` hooks and before
  the brokers start; they disconnect after the brokers stop and after the `after_shutdown=` hooks.
  `Shutdown` and `BackgroundTasks` behave as in [FastAPI](fastapi.md). `setup()` works on an
  `AsgiFastStream` too.
- **Instances.** As with `inject()`, a `Client` is one instance per container, and a
  `NotSingletonClient` is one instance per argument that declares it, not one per message.
- **The function stays a function.** Its signature shows `Annotated[UserService, Depends(...)]` to
  FastStream, as in [FastAPI](fastapi.md).
- **One app at a time.** A subscriber function and its dependencies are rewritten once, whatever the
  container, so apps that share them, e.g. an app per test on a module-level broker, run one after
  another: an app that starts while another one with the same function runs fails to start. A
  dependency function that takes clients serves either FastAPI or FastStream handlers, not both.

**Testing.** FastStream's test broker runs no app hooks, so start the app with `TestApp` inside it:

```python
# tests/test_worker.py
import pytest
from faststream import TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.worker import app, broker
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_greet(capsys: pytest.CaptureFixture[str]) -> None:
    with DI.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(1, "greetings")

    assert "Hello, alice!" in capsys.readouterr().out
```

```console
$ pytest -q tests/test_worker.py
.                                                                        [100%]
1 passed in 0.14s
```

A message handled without the app's lifespan, e.g. through `TestNatsBroker(broker)` without `TestApp`,
raises `RuntimeError: UserService is not connected: start the app with its lifespan`. A subscriber
added after the app has started raises `RuntimeError: UserService was not started with the app`.
