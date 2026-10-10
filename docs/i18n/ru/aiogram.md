# <a id="aiogram"></a>aiogram

[English](../../guide/aiogram.md) · **Русский** · [简体中文](../zh-CN/aiogram.md) · [Español](../es/aiogram.md) · [Português (Brasil)](../pt-BR/aiogram.md) · [日本語](../ja/aiogram.md) · [Polski](../pl/aiogram.md)

← [Документация](../README.ru.md#documentation)

Обработчик aiogram получает клиент по аннотации типа рядом с сообщением. Сам обработчик никак не
помечается: ни `@inject`, ни `FromDishka[...]`.

```bash
pip install "nuke-di[aiogram]"
```

Нужен aiogram 3.2 или новее. С клиентами из примеров для [FastAPI](fastapi.md):

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

С токеном от [@BotFather](https://t.me/BotFather) бот запускается командой `BOT_TOKEN=... python -m app.bot`.
Без токена тот же диспетчер запускается с `Bot`, сессия которого отвечает прямо в процессе: тестового
клиента у aiogram нет, а все запросы бота уходят через сессию.

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

Что произошло:

1. `setup(dp)` добавил по одному внутреннему middleware на каждое событие диспетчера — aiogram вызывает
   его для обработчиков всех включённых в диспетчер роутеров — и взял на себя старт и остановку диспетчера.
2. При старте `dp.emit_startup()`, который `start_polling()` вызывает первым делом, обошёл обработчики
   диспетчера и его роутеров, нашёл `users: UserService` в `start`, разрешил его и подключил клиенты,
   каждый после его собственных зависимостей.
3. Апдейт подошёл обработчику `start`, и middleware положил подключённый `UserService` в данные, которые
   aiogram передаёт обработчику по имени.
4. При остановке клиенты отключились.

Пример [aiogram_bot](../../../examples/aiogram_bot/README.md) сам запускает `start_polling()` против
фейкового Telegram.

## <a id="the-rules"></a>Правила

- **Где заполняются клиенты.** В аргументах обработчиков диспетчера и всех включённых в него роутеров на
  любой глубине, объявленных как до `setup()`, так и после, для любого вида апдейтов: сообщений,
  callback-запросов, inline-запросов, обработчиков ошибок и всего остального. Обработчиком может быть
  функция, связанный метод, функция, обёрнутая через `functools.wraps`, или обычная `def`, которую aiogram
  выполняет в потоке. Аргумент считается клиентом, если его аннотация типа — клиент, в том числе внутри
  `Annotated[UserService, ...]`.
- **Обработчики старта и остановки.** Обработчики `@dp.startup()` и `@dp.shutdown()`, а также такие же
  обработчики роутеров, тоже получают клиенты — рядом с `bot` и другими аргументами, которые им передаёт
  aiogram. Всем им aiogram передаёт одни и те же аргументы, поэтому среди них одно имя — один клиент.
- **По имени, для каждого обработчика.** aiogram передаёт обработчику элементы своих данных по имени;
  nuke-di добавляет аргументы-клиенты того обработчика, который подошёл апдейту, поэтому два обработчика
  могут получать под одним именем разные клиенты. Имя, которое aiogram передаёт сам, приводит к `TypeError`
  при старте: `bot`, `state`, `event_from_user` и другие его имена, workflow data из `Dispatcher(name=...)`
  или `dp["name"]`, а также именованные аргументы `start_polling()`.
- **Разрешаются при старте.** Клиенты разрешаются, когда стартует диспетчер: в `start_polling()`, при
  старте webhook-приложения или в `await dp.emit_startup()` в тесте. Поэтому `override()` до этого момента
  подменяет их, а импорт бота ничего не создаёт.
- **Порядок старта и остановки.** Клиенты подключаются до обработчиков старта диспетчера и его роутеров и
  отключаются после всех их обработчиков остановки, которые aiogram вызывает для роутеров после
  обработчиков диспетчера. Если обработчик старта падает, клиенты снова отключаются: после неудачного
  старта `start_polling()` остановку не вызывает. При остановке выставляется `Shutdown` и
  останавливаются `BackgroundTasks` — до отключения клиентов, как в воркере.
- **Обработчики, которые ещё работают.** `start_polling()` обрабатывает каждый апдейт в отдельной задаче и
  при остановке их не дожидается: обработчик, который всё ещё работает, когда клиенты отключаются,
  застаёт их уже отключёнными.
- **Цена апдейта.** Один поиск в словаре по обработчику и по одному элементу данных на каждый
  аргумент-клиент: около 0,3 мкс на апдейт против 22 мкс собственной диспетчеризации сообщения в aiogram
  (Python 3.14).
- **Функция остаётся функцией.** В ней ничего не переписывается; прямой вызов с клиентом, например в
  юнит-тесте, работает как раньше.
- **Другой контейнер.** `setup(dp, container)`. Два диспетчера на одном контейнере работают только по
  очереди: второй старт выбрасывает `RuntimeError: nuke-di clients failed to start: the container is already
  connected`.

## <a id="testing"></a>Тестирование

Тест подменяет клиент до старта и сам подаёт апдейты — с той же сессией, что выше:

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

`dp.feed_update()` выбрасывает то, что выбросил обработчик, если только это исключение не обработал
обработчик ошибок диспетчера.

## <a id="errors"></a>Ошибки

**Клиент, который не смог подключиться**, проваливает старт: `start_polling()` выбрасывает обычный
`RuntimeError` из `ConnectError` ещё до первого запроса к Telegram, а уже подключённые клиенты
отключаются.

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

**Остальные** ошибки выбрасываются с такими сообщениями:

| Когда | Ошибка |
|---|---|
| Апдейт пришёл до старта | ``RuntimeError: UserService is not connected: start the dispatcher with `start_polling()`, or `await dp.emit_startup()` in a test`` |
| После старта включён роутер с обработчиком клиента, которого больше никто не запрашивал | `RuntimeError: Billing was not started with the dispatcher: register its handler before the dispatcher starts` |
| Аргумент-клиент под именем, которое aiogram передаёт сам | `TypeError: Argument "state" of stateful is UserService, but aiogram passes "state" to handlers itself: rename the argument` |
| Фильтр принимает клиент | `TypeError: Argument "db" of the filter is_known is Database: aiogram calls filters before the middlewares that fill clients, so a filter takes no clients; check it in the handler instead` |
| В `setup()` передан роутер | `TypeError: setup() takes the Dispatcher, not <Router '0x109bbea50'>: the routers included into it are covered` |

## <a id="not-supported"></a>Что не поддерживается

- **Фильтры** клиенты не принимают и выбрасывают `TypeError` при старте: aiogram выполняет фильтры
  обработчика раньше его middleware, так что в данных ещё нет ни одного клиента. Проверяйте это в
  обработчике.
- **Обработчики-классы** — подклассы `MessageHandler` и других классов `BaseHandler` — клиенты не
  принимают: aiogram передаёт им данные через `self.data`, а их `__init__` принадлежит aiogram.
- **`setup()` на роутере.** Он принимает `Dispatcher`: роутер можно включить только в один диспетчер, и
  aiogram это проверяет, а middleware диспетчера покрывает все роутеры под ним.
