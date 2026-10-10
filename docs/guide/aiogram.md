# aiogram

**English** · [Русский](../i18n/ru/aiogram.md) · [简体中文](../i18n/zh-CN/aiogram.md) · [Español](../i18n/es/aiogram.md) · [Português (Brasil)](../i18n/pt-BR/aiogram.md) · [日本語](../i18n/ja/aiogram.md) · [Polski](../i18n/pl/aiogram.md)

← [Documentation](../../README.md#documentation)

An aiogram handler takes a client by its type hint, next to the message. Nothing marks the handler: no
`@inject`, no `FromDishka[...]`.

```bash
pip install "nuke-di[aiogram]"
```

Requires aiogram 3.2 or newer. With the clients of the [FastAPI](fastapi.md) examples:

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

With a token from [@BotFather](https://t.me/BotFather), `BOT_TOKEN=... python -m app.bot` runs it. Without
one, a `Bot` whose session answers in the process runs the same dispatcher: aiogram has no test client,
and a session is where a bot's requests go.

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

What happened:

1. `setup(dp)` added one inner middleware to every event of the dispatcher, which aiogram runs for the
   handlers of every router included into it, and took over the dispatcher's startup and shutdown.
2. On startup, `dp.emit_startup()`, which `start_polling()` calls first, walked the handlers of the
   dispatcher and its routers, found `users: UserService` in `start`, resolved it and connected the
   clients, each after its own dependencies.
3. The update matched `start`, and the middleware put the connected `UserService` into the data aiogram
   passes to a handler by name.
4. On shutdown the clients disconnected.

The [aiogram_bot](../../examples/aiogram_bot/README.md) example runs `start_polling()` itself against a fake
Telegram.

## The rules

- **Where clients are filled.** In the arguments of the handlers of the dispatcher and of every router
  included into it, at any depth and before `setup()` or after, for every kind of update: messages,
  callback queries, inline queries, error handlers and the rest. A handler is a function, a bound method,
  a function wrapped with `functools.wraps`, or a plain `def`, which aiogram runs in a thread. An argument
  is a client when its type hint is a client, also inside `Annotated[UserService, ...]`.
- **Startup and shutdown handlers.** `@dp.startup()` and `@dp.shutdown()` handlers, and those of the
  routers, take clients too, next to the `bot` and the other arguments aiogram passes them. aiogram passes
  them all the same arguments, so among them one name means one client.
- **By name, per handler.** aiogram passes a handler the items of its data by name; nuke-di adds the
  client arguments of the handler that matched the update, so two handlers may give different clients
  one name. A name aiogram passes itself raises `TypeError` on startup: `bot`, `state`, `event_from_user`
  and its other names, the workflow data of `Dispatcher(name=...)` or `dp["name"]`, and the keyword
  arguments of `start_polling()`.
- **Resolved on startup.** The clients are resolved when the dispatcher starts: `start_polling()`, the
  startup of the webhook app, or `await dp.emit_startup()` in a test. So `override()` before that replaces
  them, and importing the bot builds nothing.
- **Startup and shutdown order.** The clients connect before the startup handlers of the dispatcher and
  of its routers, and disconnect after all their shutdown handlers, which aiogram calls for the routers
  after the dispatcher's. A startup handler that fails disconnects them again: `start_polling()` does not
  call the shutdown after a failed startup. On shutdown `Shutdown` is set and `BackgroundTasks` are
  stopped before the clients disconnect, as in a worker.
- **Handlers still running.** `start_polling()` handles each update in a task and does not wait for them
  when it stops: a handler that is still running when the clients disconnect finds them disconnected.
- **Cost of an update.** One dictionary lookup by the handler, and one item of the data per client
  argument: about 0.3 µs per update, against 22 µs of aiogram's own dispatch of a message (Python 3.14).
- **The function stays a function.** Nothing in it is rewritten; calling it directly with a client, e.g.
  in a unit test, works as before.
- **Another container.** `setup(dp, container)`. Two dispatchers on one container run one at a time: the
  second startup raises `RuntimeError: nuke-di clients failed to start: the container is already
  connected`.

## Testing

A test replaces a client before the startup and feeds the updates itself, with the session above:

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

`dp.feed_update()` raises what the handler raised, unless an error handler of the dispatcher handles it.

## Errors

**A client that fails to connect** fails the startup: `start_polling()` raises a plain `RuntimeError` from
the `ConnectError` before its first request to Telegram, and the clients that connected are disconnected.

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

**The others**, each raised with this message:

| When | Error |
|---|---|
| An update comes before the startup | ``RuntimeError: UserService is not connected: start the dispatcher with `start_polling()`, or `await dp.emit_startup()` in a test`` |
| A router with a handler of a client nobody asked for is included after the startup | `RuntimeError: Billing was not started with the dispatcher: register its handler before the dispatcher starts` |
| A client argument under a name aiogram passes itself | `TypeError: Argument "state" of stateful is UserService, but aiogram passes "state" to handlers itself: rename the argument` |
| A filter takes a client | `TypeError: Argument "db" of the filter is_known is Database: aiogram calls filters before the middlewares that fill clients, so a filter takes no clients; check it in the handler instead` |
| `setup()` is given a router | `TypeError: setup() takes the Dispatcher, not <Router '0x109bbea50'>: the routers included into it are covered` |

## Not supported

- **Filters** take no clients, and raise `TypeError` on startup: aiogram runs a handler's filters before
  its middlewares, so no client is in the data yet. Check it in the handler.
- **Class-based handlers**, subclasses of `MessageHandler` and the other `BaseHandler` classes, take no
  clients: aiogram passes them the data as `self.data`, and their `__init__` is aiogram's.
- **`setup()` on a router.** It takes the `Dispatcher`: a router is included into one dispatcher only,
  which aiogram enforces, and the dispatcher's middleware covers the routers below it.
