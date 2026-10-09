# <a id="faststream"></a>FastStream

[English](../../guide/faststream.md) · [Русский](../ru/faststream.md) · [简体中文](../zh-CN/faststream.md) · [Español](../es/faststream.md) · [Português (Brasil)](../pt-BR/faststream.md) · **日本語** · [Polski](../pl/faststream.md)

← [ドキュメント](../README.ja.md#documentation)

FastStream のサブスクライバーは、メッセージと並んで型ヒントでクライアントを受け取ります。

```bash
pip install "nuke-di[faststream]"
```

FastStream 0.6 以降が必要で、ブローカーの種類は問いません。[FastAPI](fastapi.md) の例と同じクライアントを使います。

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

メッセージは次のコードで送信しました。

```python
# publish.py
import asyncio

from faststream.nats import NatsBroker


async def main() -> None:
    async with NatsBroker("nats://localhost:4222") as broker:
        await broker.publish(42, "greetings")


asyncio.run(main())
```

ルール：

- **クライアントが埋められる場所。** アプリのブローカーのサブスクライバー（インクルードされたルーターのものも含む）と、それらが使うすべての `Depends(...)` の引数です。深さは問いません。関数とクラスが対象で、サブスクライバー、そのルーター、ブローカーの `dependencies=` も含まれます。それ以外の引数（メッセージ、そのフィールド、`Context()`）はすべて FastStream が扱います。
- **起動するクライアント。** 起動時に、アプリのブローカーが扱うすべてのサブスクライバー（ルーターのものも含む）のクライアントが起動します。サブスクライバーは `setup(app)` の前に宣言しても後に宣言してもかまいません。
- **lifespan。** クライアントは、アプリ自身の `lifespan=` と `on_startup=` フックより前、かつブローカーの起動より前に接続し、ブローカーの停止と `after_shutdown=` フックの後に切断します。`Shutdown` と `BackgroundTasks` は [FastAPI](fastapi.md) と同じように動作します。`setup()` は `AsgiFastStream` でも使えます。
- **インスタンス。** `inject()` と同様に、`Client` はコンテナごとに 1 インスタンス、`NotSingletonClient` はそれを宣言する引数ごとに 1 インスタンスです。メッセージごとではありません。
- **関数は関数のまま。** [FastAPI](fastapi.md) と同じように、FastStream から見たシグネチャは `Annotated[UserService, Depends(...)]` になります。
- **一度に 1 つのアプリ。** サブスクライバー関数とその依存関係は、コンテナに関係なく一度だけ書き換えられます。そのため、それらを共有するアプリ（たとえばモジュールレベルのブローカーでテストごとに作るアプリ）は 1 つずつ順番に実行します。同じ関数を持つ別のアプリが動いている間に起動したアプリは、起動に失敗します。クライアントを受け取る依存関係の関数が扱えるのは、FastAPI か FastStream のどちらか一方のハンドラーで、両方ではありません。

**テスト。** FastStream のテストブローカーはアプリのフックを実行しないので、その内側で `TestApp` を使ってアプリを起動します。

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

アプリの lifespan を経由せずに処理されたメッセージ（`TestApp` を使わない `TestNatsBroker(broker)` など）は、`RuntimeError: UserService is not connected: start the app with its lifespan` を送出します。アプリの起動後に追加されたサブスクライバーは `RuntimeError: UserService was not started with the app` を送出します。
