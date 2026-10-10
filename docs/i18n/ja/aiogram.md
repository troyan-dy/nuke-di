# <a id="aiogram"></a>aiogram

[English](../../guide/aiogram.md) · [Русский](../ru/aiogram.md) · [简体中文](../zh-CN/aiogram.md) · [Español](../es/aiogram.md) · [Português (Brasil)](../pt-BR/aiogram.md) · **日本語** · [Polski](../pl/aiogram.md)

← [ドキュメント](../README.ja.md#documentation)

aiogram のハンドラーは、メッセージと並んで型ヒントでクライアントを受け取ります。ハンドラーに印を付ける必要はなく、`@inject` も `FromDishka[...]` も要りません。

```bash
pip install "nuke-di[aiogram]"
```

aiogram 3.2 以降が必要です。[FastAPI](fastapi.md) の例と同じクライアントを使います。

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

[@BotFather](https://t.me/BotFather) で取得したトークンがあれば、`BOT_TOKEN=... python -m app.bot` で起動できます。トークンがなくても、プロセス内で応答するセッションを持つ `Bot` があれば、同じディスパッチャーを動かせます。aiogram にはテストクライアントがなく、ボットのリクエストの送り先はセッションだからです。

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

何が起きたか：

1. `setup(dp)` は、ディスパッチャーのすべてのイベントに inner ミドルウェアを 1 つ追加し（aiogram はこれを、ディスパッチャーにインクルードされたすべてのルーターのハンドラーに対して実行します）、ディスパッチャーの起動と停止を引き受けました。
2. 起動時に、`start_polling()` が最初に呼び出す `dp.emit_startup()` が、ディスパッチャーとそのルーターのハンドラーをたどって `start` の `users: UserService` を見つけ、それを解決し、クライアントをそれぞれ自身の依存関係の後に接続しました。
3. アップデートが `start` にマッチし、ミドルウェアは接続済みの `UserService` を、aiogram がハンドラーに名前で渡すデータに入れました。
4. 停止時に、クライアントは切断されました。

[aiogram_bot](../../../examples/aiogram_bot/README.md) のサンプルでは、偽の Telegram を相手に `start_polling()` そのものを実行しています。

## <a id="the-rules"></a>ルール

- **クライアントが埋められる場所。** ディスパッチャーと、それにインクルードされたすべてのルーターのハンドラーの引数です。ルーターの深さは問わず、インクルードは `setup()` の前でも後でもかまいません。メッセージ、コールバッククエリ、インラインクエリ、エラーハンドラーなど、あらゆる種類のアップデートが対象です。ハンドラーは関数、束縛メソッド、`functools.wraps` でラップされた関数、または aiogram がスレッドで実行する普通の `def` のいずれでもかまいません。型ヒントがクライアントである引数がクライアントになり、`Annotated[UserService, ...]` の中にあっても同様です。
- **起動ハンドラーと停止ハンドラー。** `@dp.startup()` と `@dp.shutdown()` のハンドラーも、ルーターのそれらも、`bot` や aiogram が渡すほかの引数と並んでクライアントを受け取ります。aiogram はそれらすべてに同じ引数を渡すので、その中ではひとつの名前はひとつのクライアントを意味します。
- **名前で、ハンドラーごとに。** aiogram はデータの項目を名前でハンドラーに渡します。nuke-di が追加するのはアップデートにマッチしたハンドラーのクライアント引数なので、2 つのハンドラーが同じ名前で別々のクライアントを受け取ってもかまいません。aiogram 自身が渡す名前を使うと、起動時に `TypeError` が送出されます。該当するのは `bot`、`state`、`event_from_user` などの名前、`Dispatcher(name=...)` や `dp["name"]` のワークフローデータ、そして `start_polling()` のキーワード引数です。
- **起動時に解決。** クライアントはディスパッチャーの起動時に解決されます。つまり `start_polling()`、Webhook アプリの起動、テストでの `await dp.emit_startup()` のときです。そのため、それより前に `override()` すればクライアントを差し替えられ、ボットをインポートしただけでは何も構築されません。
- **起動と停止の順序。** クライアントは、ディスパッチャーとそのルーターの起動ハンドラーより前に接続し、すべての停止ハンドラーの後に切断します（aiogram はルーターの停止ハンドラーをディスパッチャーのものの後に呼び出します）。起動ハンドラーが失敗した場合は、クライアントを再び切断します。`start_polling()` は起動に失敗した後に停止処理を呼び出さないためです。停止時には、ワーカーと同じように、クライアントが切断される前に `Shutdown` がセットされ、`BackgroundTasks` が止められます。
- **まだ実行中のハンドラー。** `start_polling()` はアップデートを 1 つずつタスクで処理し、停止時にそれらの完了を待ちません。クライアントの切断時にまだ実行中のハンドラーからは、クライアントは切断済みに見えます。
- **アップデートあたりのコスト。** ハンドラーをキーにした辞書の検索が 1 回と、クライアント引数ごとにデータの項目が 1 つです。アップデートあたり約 0.3 µs で、aiogram 自身によるメッセージのディスパッチの 22 µs と比べてわずかです（Python 3.14）。
- **関数は関数のまま。** 関数の中身は何も書き換えられません。たとえば単体テストで、クライアントを渡して直接呼び出すことは、これまでどおりできます。
- **別のコンテナ。** `setup(dp, container)` を使います。1 つのコンテナ上の 2 つのディスパッチャーは、一度に 1 つずつしか実行できません。2 つ目の起動は `RuntimeError: nuke-di clients failed to start: the container is already connected` を送出します。

## <a id="testing"></a>テスト

テストでは、起動前にクライアントを差し替え、上のセッションを使ってアップデートを自分で流し込みます。

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

ディスパッチャーのエラーハンドラーが処理しない限り、`dp.feed_update()` はハンドラーが送出した例外をそのまま送出します。

## <a id="errors"></a>エラー

**接続に失敗したクライアント**があると、起動が失敗します。`start_polling()` は Telegram への最初のリクエストより前に、`ConnectError` を原因とする普通の `RuntimeError` を送出し、接続済みのクライアントは切断されます。

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

**その他のエラー**は、それぞれ次のメッセージで送出されます。

| 状況 | エラー |
|---|---|
| 起動前にアップデートが届いた | ``RuntimeError: UserService is not connected: start the dispatcher with `start_polling()`, or `await dp.emit_startup()` in a test`` |
| 誰も要求していないクライアントのハンドラーを持つルーターが、起動後にインクルードされた | `RuntimeError: Billing was not started with the dispatcher: register its handler before the dispatcher starts` |
| aiogram 自身が渡す名前のクライアント引数 | `TypeError: Argument "state" of stateful is UserService, but aiogram passes "state" to handlers itself: rename the argument` |
| フィルターがクライアントを受け取っている | `TypeError: Argument "db" of the filter is_known is Database: aiogram calls filters before the middlewares that fill clients, so a filter takes no clients; check it in the handler instead` |
| `setup()` にルーターが渡された | `TypeError: setup() takes the Dispatcher, not <Router '0x109bbea50'>: the routers included into it are covered` |

## <a id="not-supported"></a>サポートされていないもの

- **フィルター**はクライアントを受け取れず、起動時に `TypeError` を送出します。aiogram はハンドラーのフィルターをミドルウェアより前に実行するので、その時点ではデータにまだクライアントがありません。確認はハンドラーの中で行ってください。
- **クラスベースのハンドラー**（`MessageHandler` やほかの `BaseHandler` クラスのサブクラス）はクライアントを受け取れません。aiogram はデータを `self.data` として渡し、その `__init__` は aiogram のものだからです。
- **ルーターに対する `setup()`。** `setup()` が受け取るのは `Dispatcher` です。ルーターは 1 つのディスパッチャーにしかインクルードできず（aiogram がそれを強制します）、ディスパッチャーのミドルウェアがその下のルーターをカバーします。
