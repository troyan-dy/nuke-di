# <a id="aiogram"></a>aiogram

[English](../../guide/aiogram.md) · [Русский](../ru/aiogram.md) · **简体中文** · [Español](../es/aiogram.md) · [Português (Brasil)](../pt-BR/aiogram.md) · [日本語](../ja/aiogram.md) · [Polski](../pl/aiogram.md)

← [文档](../README.zh-CN.md#documentation)

aiogram 的处理函数在消息旁边通过类型提示接收客户端。处理函数上不需要任何标记：没有
`@inject`，也没有 `FromDishka[...]`。

```bash
pip install "nuke-di[aiogram]"
```

需要 aiogram 3.2 或更高版本。使用 [FastAPI](fastapi.md) 示例中的客户端：

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

用 [@BotFather](https://t.me/BotFather) 发放的 token，`BOT_TOKEN=... python -m app.bot` 即可运行它。
没有 token 时，一个其 session 在进程内直接应答的 `Bot` 也能运行同一个 dispatcher：aiogram 没有
测试客户端，而 session 正是 bot 的请求发往的地方。

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

发生了什么：

1. `setup(dp)` 为 dispatcher 的每种事件添加了一个内层中间件（aiogram 会为包含进 dispatcher 的
   每个路由器的处理函数运行它），并接管了 dispatcher 的启动和关闭。
2. 启动时，`dp.emit_startup()`（`start_polling()` 首先调用的就是它）遍历了 dispatcher 及其路由器的
   处理函数，在 `start` 中找到 `users: UserService`，解析它并连接各个客户端，每个客户端都在
   它自己的依赖之后连接。
3. update 匹配到了 `start`，中间件把已连接的 `UserService` 放进 aiogram 按名称传给处理函数的数据中。
4. 关闭时，客户端断开连接。

[aiogram_bot](../../../examples/aiogram_bot/README.md) 示例自己针对一个假的 Telegram 运行 `start_polling()`。

## <a id="the-rules"></a>规则

- **在哪里填充客户端。** 在 dispatcher 以及包含进它的每个路由器的处理函数的参数中，不论嵌套多深，
  不论路由器是在 `setup()` 之前还是之后包含进来，适用于每种 update：消息、回调查询、内联查询、
  错误处理函数等等。处理函数可以是函数、绑定方法、用 `functools.wraps` 包装的函数，或者普通的
  `def`（aiogram 会在线程中运行它）。类型提示是客户端的参数就是客户端参数，写在
  `Annotated[UserService, ...]` 中也一样。
- **启动和关闭处理函数。** `@dp.startup()` 和 `@dp.shutdown()` 处理函数，以及路由器上的同类处理函数，
  同样接收客户端，与 `bot` 以及 aiogram 传给它们的其他参数并列。aiogram 给它们全部传入相同的参数，
  因此在它们之间，一个名称只对应一个客户端。
- **按名称、按处理函数提供。** aiogram 按名称把数据中的各项传给处理函数；nuke-di 添加的是与该 update
  匹配的那个处理函数的客户端参数，因此两个处理函数可以用同一个名称接收不同的客户端。如果使用了
  aiogram 自己传入的名称，启动时会抛出 `TypeError`：`bot`、`state`、`event_from_user` 及其他名称，
  `Dispatcher(name=...)` 或 `dp["name"]` 中的 workflow data，以及 `start_polling()` 的关键字参数。
- **在启动时解析。** 客户端在 dispatcher 启动时解析：`start_polling()`、webhook 应用的启动，或者
  测试中的 `await dp.emit_startup()`。因此在此之前调用 `override()` 就能替换它们，而导入 bot 时
  什么都不会构建。
- **启动和关闭顺序。** 客户端在 dispatcher 及其路由器的启动处理函数之前连接，在它们所有的关闭处理函数
  之后断开；aiogram 先调用 dispatcher 的关闭处理函数，再调用路由器的。如果某个启动处理函数失败，
  客户端会重新断开：`start_polling()` 在启动失败后不会调用关闭流程。关闭时，与 worker 中一样，
  先设置 `Shutdown` 并停止 `BackgroundTasks`，然后客户端才断开。
- **仍在运行的处理函数。** `start_polling()` 在单独的任务中处理每个 update，停止时并不等待这些任务：
  客户端断开时仍在运行的处理函数会发现客户端已经断开。
- **每个 update 的开销。** 处理函数一次字典查找，每个客户端参数在数据中占一项：每个 update 约
  0.3 µs，而 aiogram 自身分发一条消息需要 22 µs（Python 3.14）。
- **函数仍然是函数。** 其中没有任何东西被重写；直接用客户端调用它（例如在单元测试中）仍和以前一样可行。
- **使用其他容器。** `setup(dp, container)`。共用一个容器的两个 dispatcher 只能依次运行：第二次启动会抛出
  `RuntimeError: nuke-di clients failed to start: the container is already
  connected`。

## <a id="testing"></a>测试

测试在启动之前替换客户端，并借助上面的 session 自己投递 update：

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

`dp.feed_update()` 会抛出处理函数抛出的异常，除非 dispatcher 的某个错误处理函数处理了它。

## <a id="errors"></a>错误

**连接失败的客户端**会使启动失败：`start_polling()` 在向 Telegram 发出第一个请求之前，从
`ConnectError` 抛出一个普通的 `RuntimeError`，已经连接的客户端会被断开。

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

**其他错误**，各自带有如下消息：

| 何时 | 错误 |
|---|---|
| update 在启动之前到达 | ``RuntimeError: UserService is not connected: start the dispatcher with `start_polling()`, or `await dp.emit_startup()` in a test`` |
| 启动之后才包含进来的路由器，其处理函数需要一个此前没人请求过的客户端 | `RuntimeError: Billing was not started with the dispatcher: register its handler before the dispatcher starts` |
| 客户端参数使用了 aiogram 自己传入的名称 | `TypeError: Argument "state" of stateful is UserService, but aiogram passes "state" to handlers itself: rename the argument` |
| 过滤器接收客户端 | `TypeError: Argument "db" of the filter is_known is Database: aiogram calls filters before the middlewares that fill clients, so a filter takes no clients; check it in the handler instead` |
| 传给 `setup()` 的是路由器 | `TypeError: setup() takes the Dispatcher, not <Router '0x109bbea50'>: the routers included into it are covered` |

## <a id="not-supported"></a>不支持的情况

- **过滤器**不接收客户端，启动时会抛出 `TypeError`：aiogram 在处理函数的中间件之前运行它的过滤器，
  此时数据中还没有任何客户端。请改在处理函数中检查。
- **基于类的处理函数**，即 `MessageHandler` 及其他 `BaseHandler` 类的子类，不接收客户端：aiogram 以
  `self.data` 的形式把数据传给它们，而它们的 `__init__` 属于 aiogram。
- **在路由器上调用 `setup()`。** 它接收的是 `Dispatcher`：一个路由器只能包含进一个 dispatcher，这由
  aiogram 强制保证，而 dispatcher 的中间件覆盖其下的所有路由器。
