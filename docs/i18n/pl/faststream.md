# <a id="faststream"></a>FastStream

[English](../../guide/faststream.md) · [Русский](../ru/faststream.md) · [简体中文](../zh-CN/faststream.md) · [Español](../es/faststream.md) · [Português (Brasil)](../pt-BR/faststream.md) · [日本語](../ja/faststream.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

Subscriber w FastStream przyjmuje klienta po adnotacji typu, obok wiadomości:

```bash
pip install "nuke-di[faststream]"
```

Wymaga FastStream 0.6 lub nowszego, z dowolnym brokerem. Z klientami z przykładów dla [FastAPI](fastapi.md):

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

Wiadomość została opublikowana tak:

```python
# publish.py
import asyncio

from faststream.nats import NatsBroker


async def main() -> None:
    async with NatsBroker("nats://localhost:4222") as broker:
        await broker.publish(42, "greetings")


asyncio.run(main())
```

Zasady:

- **Gdzie wstawiani są klienci.** W argumentach subscriberów brokerów aplikacji, także tych
  z dołączonych routerów, oraz każdego `Depends(...)`, którego używają, na dowolnej głębokości: funkcje
  i klasy, łącznie z `dependencies=` subscribera, jego routera i brokera. Każdy inny argument należy
  do FastStream: wiadomość, jej pola, `Context()`.
- **Którzy klienci startują.** Przy starcie — klienci każdego subscribera obsługiwanego przez brokery
  aplikacji, łącznie z routerami. Subscribery można deklarować przed `setup(app)` albo po nim.
- **Lifespan.** Klienci łączą się przed własnymi hookami `lifespan=` i `on_startup=` aplikacji i zanim
  wystartują brokery; rozłączają się, gdy brokery się zatrzymają, i po hookach `after_shutdown=`.
  `Shutdown` i `BackgroundTasks` działają tak jak w [FastAPI](fastapi.md). `setup()` działa także na
  `AsgiFastStream`.
- **Instancje.** Tak jak w `inject()`, `Client` to jedna instancja na kontener, a
  `NotSingletonClient` — jedna instancja na każdy argument, który go deklaruje, a nie jedna na wiadomość.
- **Funkcja pozostaje funkcją.** Jej sygnatura pokazuje FastStream `Annotated[UserService, Depends(...)]`,
  tak jak w [FastAPI](fastapi.md).
- **Jedna aplikacja naraz.** Funkcja-subscriber i jej zależności są przepisywane raz, niezależnie od
  kontenera, więc aplikacje, które je współdzielą, np. aplikacja na test z brokerem na poziomie modułu,
  działają jedna po drugiej: aplikacja, która startuje, gdy działa inna z tą samą funkcją, nie wystartuje.
  Funkcja-zależność, która przyjmuje klientów, obsługuje handlery albo FastAPI, albo FastStream, nie oba naraz.

**Testowanie.** Testowy broker FastStream nie uruchamia hooków aplikacji, więc wystartuj w nim aplikację
przez `TestApp`:

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

Wiadomość obsłużona z pominięciem lifespan aplikacji, np. przez `TestNatsBroker(broker)` bez `TestApp`,
zgłasza `RuntimeError: UserService is not connected: start the app with its lifespan`. Subscriber
dodany po starcie aplikacji zgłasza `RuntimeError: UserService was not started with the app`.
