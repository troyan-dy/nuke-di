# <a id="aiogram"></a>aiogram

[English](../../guide/aiogram.md) · [Русский](../ru/aiogram.md) · [简体中文](../zh-CN/aiogram.md) · [Español](../es/aiogram.md) · [Português (Brasil)](../pt-BR/aiogram.md) · [日本語](../ja/aiogram.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

Handler w aiogram przyjmuje klienta po adnotacji typu, obok wiadomości. Handlera nic nie oznacza: żadnego
`@inject`, żadnego `FromDishka[...]`.

```bash
pip install "nuke-di[aiogram]"
```

Wymaga aiogram 3.2 lub nowszego. Z klientami z przykładów dla [FastAPI](fastapi.md):

```python
# app/bot.py
import asyncio
import os

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.clients import UserService
from nuke_di.aiogram import setup

router = Router()


@router.message(CommandStart())
async def start(message: Message, users: UserService) -> None:
    await message.answer(await users.greet(message.chat.id))


dp = Dispatcher()
dp.include_router(router)
setup(dp)  # clients connect before the startup handlers, disconnect after the shutdown handlers


async def main() -> None:
    await dp.start_polling(Bot(os.environ["BOT_TOKEN"]))


if __name__ == "__main__":
    asyncio.run(main())
```

Z tokenem od [@BotFather](https://t.me/BotFather) bota uruchamia `BOT_TOKEN=... python -m app.bot`. Bez
tokenu ten sam dispatcher uruchamia `Bot`, którego sesja odpowiada wewnątrz procesu: aiogram nie ma
klienta testowego, a sesja to miejsce, do którego trafiają żądania bota.

```python
# app/telegram.py
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Update


class PrintingSession(BaseSession):
    """
    The Bot API of a test: prints what the bot sends, and keeps it in `sent`, instead of calling Telegram.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        assert isinstance(method, SendMessage), method
        print(f"bot -> chat {method.chat_id}: {method.text}")
        self.sent.append(method.text)
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        pass


def message(bot: Bot, text: str, chat_id: int = 42) -> Update:
    """
    An update with a message in a private chat, as Telegram sends it.
    """
    chat = {"id": chat_id, "type": "private"}
    return Update.model_validate(
        {"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": chat, "text": text}},
        context={"bot": bot},
    )
```

```python
# app/try_bot.py
import asyncio

from aiogram import Bot

from app.bot import dp
from app.telegram import PrintingSession, message


async def main() -> None:
    bot = Bot("42:TEST", session=PrintingSession())
    await dp.emit_startup(bot=bot)  # what start_polling() does first
    try:
        await dp.feed_update(bot, message(bot, "/start"))  # and then with every update
    finally:
        await dp.emit_shutdown(bot=bot)


asyncio.run(main())
```

```console
$ python -m app.try_bot
database: connected
bot -> chat 42: Hello, user-42!
database: disconnected
```

Co się stało:

1. `setup(dp)` dodał jeden wewnętrzny middleware do każdego zdarzenia dispatchera, który aiogram uruchamia
   dla handlerów każdego dołączonego do niego routera, i przejął start oraz zatrzymanie dispatchera.
2. Przy starcie `dp.emit_startup()`, które `start_polling()` wywołuje na początku, przeszedł po handlerach
   dispatchera i jego routerów, znalazł `users: UserService` w `start`, rozwiązał go i połączył klientów,
   każdego po jego własnych zależnościach.
3. Update pasował do `start`, a middleware włożył połączony `UserService` do danych, które aiogram
   przekazuje handlerowi po nazwie.
4. Przy zatrzymaniu klienci się rozłączyli.

Przykład [aiogram_bot](../../../examples/aiogram_bot/README.md) sam uruchamia `start_polling()` na
podstawionym Telegramie.

## <a id="the-rules"></a>Zasady

- **Gdzie wstawiani są klienci.** W argumentach handlerów dispatchera i każdego dołączonego do niego routera,
  na dowolnej głębokości, przed `setup()` lub po nim, dla każdego rodzaju update'u: wiadomości, callback
  query, inline query, handlerów błędów i pozostałych. Handler to funkcja, metoda związana, funkcja
  opakowana przez `functools.wraps` albo zwykłe `def`, które aiogram uruchamia w wątku. Argument jest
  klientem, gdy jego adnotacja typu jest klientem, także wewnątrz `Annotated[UserService, ...]`.
- **Handlery startu i zatrzymania.** Handlery `@dp.startup()` i `@dp.shutdown()`, a także te z routerów,
  również przyjmują klientów, obok `bot` i pozostałych argumentów, które przekazuje im aiogram. aiogram
  przekazuje im wszystkim te same argumenty, więc wśród nich jedna nazwa oznacza jednego klienta.
- **Po nazwie, osobno dla każdego handlera.** aiogram przekazuje handlerowi elementy jego danych po nazwie;
  nuke-di dodaje argumenty-klientów handlera, który pasował do update'u, więc dwa handlery mogą pod jedną
  nazwą dostać różnych klientów. Nazwa, którą aiogram przekazuje sam, zgłasza `TypeError` przy starcie:
  `bot`, `state`, `event_from_user` i jego pozostałe nazwy, workflow data z `Dispatcher(name=...)` lub
  `dp["name"]` oraz argumenty nazwane `start_polling()`.
- **Rozwiązywani przy starcie.** Klienci są rozwiązywani, gdy dispatcher startuje: w `start_polling()`,
  przy starcie aplikacji webhooka albo w `await dp.emit_startup()` w teście. Dlatego `override()`
  wywołany wcześniej ich podmienia, a import bota niczego nie buduje.
- **Kolejność startu i zatrzymania.** Klienci łączą się przed handlerami startu dispatchera i jego
  routerów, a rozłączają się po wszystkich ich handlerach zatrzymania, które aiogram wywołuje dla routerów
  po handlerach dispatchera. Handler startu, który się nie powiedzie, z powrotem ich rozłącza:
  `start_polling()` nie wywołuje zatrzymania po nieudanym starcie. Przy zatrzymaniu `Shutdown` zostaje
  ustawiony, a `BackgroundTasks` zatrzymane, zanim klienci się rozłączą, tak jak w workerze.
- **Handlery, które wciąż działają.** `start_polling()` obsługuje każdy update w osobnym tasku i nie czeka
  na nie przy zatrzymaniu: handler, który wciąż działa, gdy klienci się rozłączają, zastaje ich rozłączonych.
- **Koszt update'u.** Jedno wyszukanie w słowniku po handlerze i jeden element danych na każdy
  argument-klienta: około 0,3 µs na update, wobec 22 µs własnego dispatchu wiadomości w aiogram
  (Python 3.14).
- **Funkcja pozostaje funkcją.** Nic w niej nie jest przepisywane; bezpośrednie wywołanie z klientem,
  np. w teście jednostkowym, działa tak jak wcześniej.
- **Inny kontener.** `setup(dp, container)`. Dwa dispatchery na jednym kontenerze działają jeden po
  drugim: drugi start zgłasza `RuntimeError: nuke-di clients failed to start: the container is already
  connected`.

## <a id="testing"></a>Testowanie

Test podmienia klienta przed startem i sam podaje update'y, z sesją pokazaną wyżej:

```python
# tests/test_bot.py
from aiogram import Bot

from app.bot import dp
from app.clients import Database
from app.telegram import PrintingSession, message
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_start() -> None:
    session = PrintingSession()
    bot = Bot("42:TEST", session=session)
    with DI.override(Database, FakeDatabase()):
        await dp.emit_startup(bot=bot)
        try:
            await dp.feed_update(bot, message(bot, "/start"))
        finally:
            await dp.emit_shutdown(bot=bot)

    assert session.sent == ["Hello, alice!"]
```

```console
$ pytest -q tests/test_bot.py
.                                                                        [100%]
1 passed in 1.35s
```

`dp.feed_update()` zgłasza to, co zgłosił handler, chyba że obsłuży to handler błędów dispatchera.

## <a id="errors"></a>Błędy

**Klient, który nie zdoła się połączyć,** przerywa start: `start_polling()` zgłasza zwykły `RuntimeError`
z `ConnectError`, zanim wyśle pierwsze żądanie do Telegrama, a klienci, którzy zdążyli się połączyć,
zostają rozłączeni.

```python
# app/broken.py
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from nuke_di import Client
from nuke_di.aiogram import setup


class Kafka(Client):
    async def connect(self) -> None:
        raise OSError("broker kafka-1:9092 is unreachable")


dp = Dispatcher()
setup(dp)


@dp.message()
async def publish(message: Message, kafka: Kafka) -> None: ...


if __name__ == "__main__":
    # The startup fails before the first request to Telegram, so any token of the right shape shows it
    asyncio.run(dp.start_polling(Bot("42:TEST")))
```

```console
$ python -m app.broken
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
nuke_di.errors.ConnectError: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ...
RuntimeError: nuke-di clients failed to start: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
$ echo $?
1
```

**Pozostałe**, każdy zgłaszany z takim komunikatem:

| Kiedy | Błąd |
|---|---|
| Update przychodzi przed startem | ``RuntimeError: UserService is not connected: start the dispatcher with `start_polling()`, or `await dp.emit_startup()` in a test`` |
| Router z handlerem klienta, o którego nikt nie prosił, zostaje dołączony po starcie | `RuntimeError: Billing was not started with the dispatcher: register its handler before the dispatcher starts` |
| Argument-klient pod nazwą, którą aiogram przekazuje sam | `TypeError: Argument "state" of stateful is UserService, but aiogram passes "state" to handlers itself: rename the argument` |
| Filtr przyjmuje klienta | `TypeError: Argument "db" of the filter is_known is Database: aiogram calls filters before the middlewares that fill clients, so a filter takes no clients; check it in the handler instead` |
| `setup()` dostaje router | `TypeError: setup() takes the Dispatcher, not <Router '0x109bbea50'>: the routers included into it are covered` |

## <a id="not-supported"></a>Nieobsługiwane

- **Filtry** nie przyjmują klientów i zgłaszają `TypeError` przy starcie: aiogram uruchamia filtry
  handlera przed jego middleware'ami, więc w danych nie ma jeszcze żadnego klienta. Sprawdź to w handlerze.
- **Handlery oparte na klasach**, podklasy `MessageHandler` i pozostałych klas `BaseHandler`, nie
  przyjmują klientów: aiogram przekazuje im dane jako `self.data`, a ich `__init__` należy do aiogram.
- **`setup()` na routerze.** Przyjmuje `Dispatcher`: router może być dołączony tylko do jednego
  dispatchera, czego aiogram pilnuje, a middleware dispatchera obejmuje routery poniżej niego.
