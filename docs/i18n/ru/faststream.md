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
