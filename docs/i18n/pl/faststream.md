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

## <a id="publishing-from-a-client"></a>Publikowanie z klienta

Klient, który publikuje — outbox albo notifier — przyjmuje broker aplikacji w `__init__`, a jego cykl
życia zostawia FastStream:

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

Wiadomość została opublikowana przez `publish.py` z przykładu powyżej. W teście testowy broker
kieruje to, co publikuje klient, tak jak każdą inną wiadomość, albo klient zostaje podmieniony:

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

Zasady:

- **Broker należy do aplikacji.** FastStream uruchamia go po połączeniu klientów i zatrzymuje, zanim
  klienci się rozłączą. Klient publikuje ze swoich metod, gdy aplikacja działa; `publish()` w jego
  `connect()` lub `disconnect()` zgłasza `faststream.exceptions.IncorrectState`, bo broker jeszcze nie
  wystartował albo jest już zatrzymany.
- **Broker jest argumentem domyślnym**, a nie klientem: nuke-di wypełnia argumenty z typem klienta,
  a pozostałym zostawia ich wartości domyślne. Test jednostkowy może zbudować
  `Notifications(nats=AsyncMock())`.
- **Pod `TestNatsBroker`** patchowany jest ten sam obiekt brokera, więc klient publikuje w pamięci,
  a `show.mock` widzi wiadomość; `override(Notifications, ...)` podmienia klienta dla każdego
  subscribera, który go przyjmuje.
- **Proces bez aplikacji FastStream**, np. `@job`, który wysyła wiadomości, sam zarządza swoim
  połączeniem: tam broker jest tworzony w `connect()` klienta i zatrzymywany w jego `disconnect()`,
  jak `Nats` w [examples/faststream_nats/publish.py](../../../examples/faststream_nats/publish.py).
- **Kilka brokerów**, `FastStream(first, second)`, działa tak samo: `setup(app)` wypełnia subscribery
  każdego z nich, a klient przyjmuje jako wartość domyślną broker, do którego publikuje.

## <a id="one-app-at-a-time-in-tests"></a>Jedna aplikacja naraz w testach

FastStream buduje subscriber od nowa przy każdym starcie, więc nuke-di przepisuje funkcję-subscriber
raz, niezależnie od kontenera (`per_container=False` w [Pisanie integracji](integrations.md)), a każda
startująca aplikacja wypełnia ją ze swojego kontenera. Testy, które startują aplikacje jedna po
drugiej, mogą więc dać każdej z nich własny kontener, na brokerze z poziomu modułu:

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

Co z tym zrobić:

- **Uruchamiaj testy, które startują aplikację, jeden po drugim** — tak właśnie robi pytest.
  pytest-xdist uruchamia je we własnych procesach, które niczego nie współdzielą.
- **Nie startuj jednocześnie dwóch aplikacji na tych samych funkcjach-subscriberach**, np. `TestApp`
  wewnątrz innego. Druga aplikacja nie wystartuje i zgłosi `RuntimeError: nuke-di clients
  failed to start: UserService is filled for another app that is running; apps that share a handler
  function run one at a time`, zamiast dostać klientów pierwszej aplikacji.
- **`DI.override()` na aplikacji z poziomu modułu albo kontener na test** — oba podejścia są
  w porządku; wybierz według tego, czego używa reszta testów.
- W odróżnieniu od tego FastAPI przepisuje funkcję dla każdego kontenera, a jego aplikacje na różnych
  kontenerach mogą działać jednocześnie; jedyny wyjątek opisuje [FastAPI](fastapi.md#an-app-per-test-container).
