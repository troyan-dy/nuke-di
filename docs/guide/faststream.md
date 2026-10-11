# FastStream

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

## Publishing from a client

A client that publishes, an outbox or a notifier, takes the app's broker in `__init__` and leaves its
lifecycle to FastStream:

```python
# app/notify.py
from faststream import FastStream
from faststream.nats import NatsBroker

from app.clients import UserService
from nuke_di import Client
from nuke_di.faststream import setup

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
setup(app)


class Notifications(Client):
    # The app's broker: FastStream starts it after the clients connect and stops it before they
    # disconnect, so connect() and disconnect() leave it alone
    def __init__(self, nats: NatsBroker = broker) -> None:
        self._nats = nats

    async def send(self, text: str) -> None:
        await self._nats.publish(text, "notifications")


@broker.subscriber("greetings")
async def greet(user_id: int, users: UserService, notifications: Notifications) -> None:
    await notifications.send(await users.greet(user_id))


@broker.subscriber("notifications")
async def show(text: str) -> None:
    print(f"notification: {text}")
```

```console
$ faststream run app.notify:app
database: connected
2026-10-10 18:39:08,837 INFO     - FastStream app starting...
2026-10-10 18:39:08,842 INFO     - greetings     |            - `Greet` waiting for messages
2026-10-10 18:39:08,843 INFO     - notifications |            - `Show` waiting for messages
2026-10-10 18:39:08,843 INFO     - FastStream app started successfully! To exit, press CTRL+C
2026-10-10 18:39:11,811 INFO     - greetings     | 7c3cf44a-9 - Received
2026-10-10 18:39:11,811 INFO     - greetings     | 7c3cf44a-9 - Processed
2026-10-10 18:39:11,812 INFO     - notifications | 5ddab782-7 - Received
notification: Hello, user-42!
2026-10-10 18:39:11,812 INFO     - notifications | 5ddab782-7 - Processed
^C
2026-10-10 18:39:12,908 INFO     - FastStream app shutting down...
2026-10-10 18:39:12,909 INFO     - FastStream app shut down gracefully.
database: disconnected
```

The message was published with the `publish.py` above. In a test, the test broker routes what the
client publishes like any other message, or the client is replaced:

```python
# tests/test_notify.py
from faststream import TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.notify import Notifications, app, broker, show
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


class FakeNotifications(Notifications):
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


async def test_greet_publishes() -> None:
    with DI.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(1, "greetings")

            show.mock.assert_called_once_with("Hello, alice!")


async def test_greet_with_fake_notifications() -> None:
    fake = FakeNotifications()
    with DI.override(Notifications, fake):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(42, "greetings")

    assert fake.sent == ["Hello, user-42!"]
```

```console
$ pytest -q tests/test_notify.py
..                                                                       [100%]
2 passed in 0.19s
```

The rules:

- **The broker is the app's.** FastStream starts it after the clients connect and stops it before they
  disconnect, so this client does not create its broker in `connect()` as a client of a third-party object
  otherwise does ([ADR-0005](../adr/0005-third-party-objects-as-client-classes.md)): FastStream owns it.
  A client publishes from its methods, while the app runs.
- **Not from `connect()` or `disconnect()`.** On a real broker, a `publish()` in `connect()` raises
  `faststream.exceptions.IncorrectState`, since the broker is not started yet, and the app fails to start
  with `RuntimeError: nuke-di clients failed to start: Notifications.connect() raised IncorrectState`. In
  `disconnect()` it raises the same, since the broker is stopped already, but nuke-di only logs a failed
  `disconnect()` and the app exits normally. Under `TestNatsBroker` the first raises
  ``SetupError: You should setup `HandlerItem` at first.`` and the second passes silently, so the tests do
  not catch a publish in `disconnect()`.
- **The broker is a default argument**, not a client: nuke-di fills the arguments typed as clients and
  leaves the others to their defaults. A unit test can build `Notifications(nats=AsyncMock())`.
- **Under `TestNatsBroker`** the same broker object is patched, so the client publishes in memory and
  `show.mock` sees the message; `override(Notifications, ...)` replaces the client for every subscriber
  that takes it.
- **A process without a FastStream app**, e.g. a `@job` that sends messages, owns its connection: there
  the broker is created in the client's `connect()` and stopped in its `disconnect()`, as `Nats` in
  [examples/faststream_nats/publish.py](../../examples/faststream_nats/publish.py).
- **Several brokers**, `FastStream(first, second)`, work the same way: `setup(app)` fills the
  subscribers of each of them, and a client takes the broker it publishes to as its default.

## One app at a time, in tests

FastStream builds a subscriber again on every start, so nuke-di rewrites a subscriber function once,
whatever the container (`per_container=False` in [Writing an integration](integrations.md)), and every
app that starts fills it from its own container. Tests that start apps one after another can therefore
give each one a container of its own, on the module-level broker:

```python
# tests/test_containers.py
from faststream import FastStream, TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.notify import broker, show
from nuke_di import Dependencies
from nuke_di.faststream import setup


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def make_app(container: Dependencies) -> FastStream:
    # A new app on the module-level broker, whose subscribers are declared on import
    app = FastStream(broker)
    setup(app, container)
    return app


async def test_greet(di: Dependencies) -> None:
    with di.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(make_app(di)):
            await test_broker.publish(1, "greetings")

            show.mock.assert_called_once_with("Hello, alice!")
```

```console
$ pytest -q tests/test_containers.py
.                                                                        [100%]
1 passed in 0.15s
```

What to do about it:

- **Run the tests that start an app one after another**, which is what pytest does. pytest-xdist runs
  them in processes of their own, which share nothing.
- **Do not start two apps on the same subscriber functions at once**, e.g. a `TestApp` inside another
  one. The second fails to start with `RuntimeError: nuke-di clients
  failed to start: UserService is filled for another app that is running; apps that share a handler
  function run one at a time`, rather than hand the first app's clients to the second.
- **`DI.override()` on the module-level app or a container per test** are both fine; choose by what
  the rest of the tests use.
- Unlike this, a FastAPI request gets the clients of the app it came to, so FastAPI apps on different
  containers serve the same functions at once, see [FastAPI](fastapi.md#an-app-per-test-container).
