# <a id="faststream"></a>FastStream

[English](../../guide/faststream.md) · **Русский** · [简体中文](../zh-CN/faststream.md) · [Español](../es/faststream.md) · [Português (Brasil)](../pt-BR/faststream.md) · [日本語](../ja/faststream.md) · [Polski](../pl/faststream.md)

← [Документация](../README.ru.md#documentation)

Подписчик FastStream получает клиент по аннотации типа рядом с сообщением:

```bash
pip install "nuke-di[faststream]"
```

Нужен FastStream 0.6 или новее, с любым брокером. С клиентами из примеров для [FastAPI](fastapi.md):

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

Сообщение было опубликовано так:

```python
# publish.py
import asyncio

from faststream.nats import NatsBroker


async def main() -> None:
    async with NatsBroker("nats://localhost:4222") as broker:
        await broker.publish(42, "greetings")


asyncio.run(main())
```

Правила:

- **Где заполняются клиенты.** В аргументах подписчиков брокеров приложения, в том числе подписчиков
  включённых роутеров, и всех `Depends(...)`, которые они используют, на любой глубине: функций и классов,
  включая `dependencies=` подписчика, его роутера и брокера. Все остальные аргументы достаются
  FastStream: сообщение, его поля, `Context()`.
- **Какие клиенты стартуют.** При старте — клиенты всех подписчиков, которых обслуживают брокеры
  приложения, включая роутеры. Подписчиков можно объявлять как до, так и после `setup(app)`.
- **Lifespan.** Клиенты подключаются до собственных хуков `lifespan=` и `on_startup=` приложения и до
  старта брокеров; отключаются после остановки брокеров и после хуков `after_shutdown=`.
  `Shutdown` и `BackgroundTasks` ведут себя так же, как в [FastAPI](fastapi.md). `setup()` работает и с
  `AsgiFastStream`.
- **Экземпляры.** Как и с `inject()`, `Client` — это один экземпляр на контейнер, а
  `NotSingletonClient` — один экземпляр на каждый объявляющий его аргумент, а не на каждое сообщение.
- **Функция остаётся функцией.** Её сигнатура показывает FastStream `Annotated[UserService, Depends(...)]`,
  как в [FastAPI](fastapi.md).
- **Одно приложение за раз.** Функция-подписчик и её зависимости переписываются один раз, каким бы ни был
  контейнер, поэтому приложения, которые их разделяют, например по приложению на тест с брокером на
  уровне модуля, запускаются по очереди: приложение, которое стартует, пока работает другое с той же
  функцией, не запускается. Функция-зависимость, которая принимает клиенты, обслуживает обработчики либо
  FastAPI, либо FastStream, но не те и другие сразу.

**Тестирование.** Тестовый брокер FastStream не запускает хуки приложения, поэтому запускайте
приложение внутри него через `TestApp`:

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

Сообщение, обработанное без lifespan приложения, например через `TestNatsBroker(broker)` без `TestApp`,
выбрасывает `RuntimeError: UserService is not connected: start the app with its lifespan`. Подписчик,
добавленный после старта приложения, выбрасывает `RuntimeError: UserService was not started with the app`.

## <a id="publishing-from-a-client"></a>Публикация из клиента

Клиент, который публикует сообщения, например outbox или нотификатор, принимает брокер приложения в
`__init__` и оставляет управление его жизненным циклом FastStream:

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

Сообщение было опубликовано через `publish.py` выше. В тесте тестовый брокер маршрутизирует то, что
публикует клиент, как любое другое сообщение, либо клиент подменяется:

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

Правила:

- **Брокер принадлежит приложению.** FastStream запускает его после подключения клиентов и
  останавливает до их отключения, поэтому этот клиент не создаёт свой брокер в `connect()`, как иначе
  делает клиент стороннего объекта ([ADR-0005](../../adr/0005-third-party-objects-as-client-classes.md)):
  им владеет FastStream. Клиент публикует из своих методов, пока приложение работает.
- **Не из `connect()` или `disconnect()`.** На настоящем брокере `publish()` в `connect()` выбрасывает
  `faststream.exceptions.IncorrectState`, поскольку брокер ещё не запущен, и приложение не стартует с
  `RuntimeError: nuke-di clients failed to start: Notifications.connect() raised IncorrectState`. В
  `disconnect()` он выбрасывает то же самое, поскольку брокер уже остановлен, но nuke-di лишь логирует
  упавший `disconnect()`, и приложение завершается штатно. Под `TestNatsBroker` первый выбрасывает
  ``SetupError: You should setup `HandlerItem` at first.``, а второй молча проходит, так что тесты не
  ловят публикацию в `disconnect()`.
- **Брокер — значение аргумента по умолчанию**, а не клиент: nuke-di заполняет аргументы, аннотированные
  клиентами, а остальным оставляет значения по умолчанию. Юнит-тест может построить
  `Notifications(nats=AsyncMock())`.
- **Под `TestNatsBroker`** патчится тот же объект брокера, поэтому клиент публикует в памяти, а
  `show.mock` видит сообщение; `override(Notifications, ...)` подменяет клиент для всех подписчиков,
  которые его принимают.
- **Процесс без приложения FastStream**, например `@job`, который отправляет сообщения, сам владеет
  своим соединением: там брокер создаётся в `connect()` клиента и останавливается в его `disconnect()`,
  как `Nats` в [examples/faststream_nats/publish.py](../../../examples/faststream_nats/publish.py).
- **Несколько брокеров**, `FastStream(first, second)`, работают так же: `setup(app)` заполняет
  подписчиков каждого из них, а клиент принимает брокер, в который публикует, как значение по умолчанию.

## <a id="one-app-at-a-time-in-tests"></a>Одно приложение за раз в тестах

FastStream заново строит подписчика при каждом старте, поэтому nuke-di переписывает функцию-подписчик
один раз, каким бы ни был контейнер (`per_container=False` на странице [Своя интеграция](integrations.md)),
и каждое стартующее приложение заполняет её из своего контейнера. Поэтому тесты, которые запускают
приложения одно за другим, могут дать каждому собственный контейнер на брокере уровня модуля:

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

Что с этим делать:

- **Запускайте тесты, которые стартуют приложение, один за другим** — pytest так и делает. pytest-xdist
  запускает их в отдельных процессах, у которых нет ничего общего.
- **Не запускайте одновременно два приложения на одних и тех же функциях-подписчиках**, например `TestApp`
  внутри другого. Второе не стартует с `RuntimeError: nuke-di clients
  failed to start: UserService is filled for another app that is running; apps that share a handler
  function run one at a time`, вместо того чтобы отдать второму клиенты первого приложения.
- **`DI.override()` на приложении уровня модуля и контейнер на тест** подходят оба; выбирайте по тому,
  что используют остальные тесты.
- В отличие от этого, запрос к FastAPI получает клиенты того приложения, в которое пришёл, поэтому
  приложения FastAPI на разных контейнерах обслуживают одни и те же функции одновременно, см.
  [FastAPI](fastapi.md#an-app-per-test-container).
