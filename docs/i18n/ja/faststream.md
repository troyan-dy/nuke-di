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

## <a id="publishing-from-a-client"></a>クライアントからのパブリッシュ

パブリッシュするクライアント（アウトボックスや通知の送信など）は、`__init__` でアプリのブローカーを受け取り、そのライフサイクルは FastStream に任せます。

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

メッセージは上の `publish.py` で送信しました。テストでは、クライアントがパブリッシュしたものをテストブローカーが他のメッセージと同じようにルーティングします。あるいは、クライアントを差し替えます。

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

ルール：

- **ブローカーはアプリのもの。** FastStream はクライアントが接続した後にブローカーを起動し、クライアントが切断する前に停止します。そのため、このクライアントは、サードパーティのオブジェクトのクライアントがほかの場合にするように `connect()` でブローカーを作成することはしません（[ADR-0005](../../adr/0005-third-party-objects-as-client-classes.md)）。ブローカーは FastStream が所有します。クライアントは、アプリの実行中にメソッドからパブリッシュします。
- **`connect()` や `disconnect()` からはパブリッシュしない。** 実際のブローカーでは、`connect()` の中の `publish()` は、ブローカーがまだ起動していないため `faststream.exceptions.IncorrectState` を送出し、アプリは `RuntimeError: nuke-di clients failed to start: Notifications.connect() raised IncorrectState` で起動に失敗します。`disconnect()` の中でも、ブローカーがすでに停止しているため同じ例外が送出されますが、nuke-di は失敗した `disconnect()` をログに記録するだけなので、アプリは正常に終了します。`TestNatsBroker` の下では、前者は ``SetupError: You should setup `HandlerItem` at first.`` を送出し、後者は何事もなく通ってしまうため、`disconnect()` の中のパブリッシュはテストでは検出できません。
- **ブローカーはデフォルト引数**であり、クライアントではありません。nuke-di はクライアント型の引数を埋め、それ以外の引数はデフォルト値のままにします。単体テストでは `Notifications(nats=AsyncMock())` を構築できます。
- **`TestNatsBroker` の下では**同じブローカーオブジェクトにパッチが当てられるので、クライアントはメモリ内でパブリッシュし、`show.mock` がそのメッセージを受け取ります。`override(Notifications, ...)` は、そのクライアントを受け取るすべてのサブスクライバーに対してクライアントを差し替えます。
- **FastStream アプリを持たないプロセス**（たとえばメッセージを送る `@job`）は、自分の接続を自分で持ちます。その場合、ブローカーはクライアントの `connect()` で作成し、`disconnect()` で停止します。[examples/faststream_nats/publish.py](../../../examples/faststream_nats/publish.py) の `Nats` がその例です。
- **複数のブローカー**（`FastStream(first, second)`）も同じように動作します。`setup(app)` はそれぞれのブローカーのサブスクライバーを埋め、クライアントはパブリッシュ先のブローカーをデフォルト値として受け取ります。

## <a id="one-app-at-a-time-in-tests"></a>テストでは一度に 1 つのアプリ

FastStream は起動のたびにサブスクライバーを構築し直すので、nuke-di はコンテナに関係なくサブスクライバー関数を一度だけ書き換え（[インテグレーションを書く](integrations.md)の `per_container=False`）、起動したアプリはそれぞれ自分のコンテナからそれを埋めます。そのため、アプリを順番に起動するテストでは、モジュールレベルのブローカーのまま、各アプリに専用のコンテナを与えることができます。

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

対処法：

- **アプリを起動するテストは 1 つずつ順番に実行する。** pytest はそのように実行します。pytest-xdist はテストをそれぞれ別のプロセスで実行し、プロセス同士は何も共有しません。
- **同じサブスクライバー関数を使う 2 つのアプリを同時に起動しない。** たとえば、`TestApp` の中で別の `TestApp` を起動する場合です。2 つ目のアプリは、最初のアプリのクライアントを受け取るのではなく、`RuntimeError: nuke-di clients failed to start: UserService is filled for another app that is running; apps that share a handler function run one at a time` で起動に失敗します。
- **モジュールレベルのアプリでの `DI.override()` も、テストごとのコンテナも**、どちらでもかまいません。他のテストで使っている方に合わせて選んでください。
- これとは異なり、FastAPI のリクエストは届いた先のアプリのクライアントを受け取るので、異なるコンテナ上の FastAPI アプリは同じ関数を同時に提供できます。[FastAPI](fastapi.md#an-app-per-test-container) を参照してください。
