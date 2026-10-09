# <a id="fastapi"></a>FastAPI

[English](../../guide/fastapi.md) · [Русский](../ru/fastapi.md) · [简体中文](../zh-CN/fastapi.md) · [Español](../es/fastapi.md) · [Português (Brasil)](../pt-BR/fastapi.md) · **日本語** · [Polski](../pl/fastapi.md)

← [ドキュメント](../README.ja.md#documentation)

FastAPI のパスオペレーションは、ジョブと同じように型ヒントでクライアントを受け取ります。ハンドラーごとに書き足すものは何もありません。`Depends` も `inject()` も不要です。

```bash
pip install "nuke-di[fastapi]"
```

FastAPI 0.105 以降が必要です。例では次のクライアントモジュールを共通で使います。

```python
# app/clients.py
from nuke_di import Client


class Database(Client):
    async def connect(self) -> None:
        print("database: connected")

    async def disconnect(self) -> None:
        print("database: disconnected")

    async def fetch_user(self, user_id: int) -> str:
        return f"user-{user_id}"


class UserService(Client):
    def __init__(self, db: Database) -> None:
        self._db = db

    async def greet(self, user_id: int) -> str:
        return f"Hello, {await self._db.fetch_user(user_id)}!"
```

API 本体：

```python
# app/api.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di.fastapi import ClientRouter, setup

app = FastAPI()
setup(app)  # before the routes: clients connect on startup, disconnect on shutdown


@app.get("/users/{user_id}")
async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


account = ClientRouter(prefix="/me")


@account.get("")
async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user


app.include_router(account)
```

```console
$ uvicorn app.api:app
INFO:     Started server process [80948]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:54682 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:54684 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [80948]
```

```console
$ curl localhost:8000/users/42
"Hello, user-42!"
$ curl localhost:8000/me -H "X-User-Id: 7"
"user-7"
```

何が起きたのか：

1. `setup(app)` によって、それ以降に `app` で宣言されるすべてのルートが、クライアント引数をグローバルな `DI` から埋めるようになり、アプリの lifespan がラップされました。
2. `@app.get` は `users: UserService` を見つけて記録しただけで、インポート時には何も構築されませんでした。
3. 起動時に lifespan が、アプリが提供するルート（アプリ自身のルートと、インクルードしたルーターのルート）のクライアントを解決し、レイヤーごとに接続しました。終了時にはそれらを切断しました。
4. `/users/42` へのリクエストは接続済みの `UserService` を受け取りました。`/me` は依存関係 `current_user` を経由し、`current_user` も同じ方法で `db: Database` を受け取ります。

ルール：

- **クライアントが埋められる場所。** パスオペレーション、WebSocket エンドポイントと、それらが使うすべての依存関係の引数です。深さは問いません。関数のほか、`Depends(Auth)` や `Annotated[Auth, Depends()]` として使われるクラスも対象で、ルート、そのルーター、`include_router()`、アプリの `dependencies=` も含まれます。型ヒントがクライアントである引数はクライアントとして扱われ、`Depends` を伴わない `Annotated[UserService, ...]` の中にある場合も同様です。それ以外の引数（パス、クエリ、ヘッダー、ボディ、`Depends`）はすべて FastAPI が扱います。
- **ルーター。** ルーターは、`APIRouter` と同じ引数を受け取る `ClientRouter(...)` で作成し、アプリか別の `ClientRouter` にインクルードします。他のルーターをインクルードしないルーターであれば、`APIRouter(route_class=ClientRoute)` でも動作します。別のコンテナを使う場合は、`setup(app, container)` と `ClientRouter(container=container)` を使います。別のコンテナのルーターをインクルードすると、ただちに `TypeError` が送出されます。
- **`setup(app)` はルートより前に呼び出す。** それより前に宣言された、クライアントを受け取るルートは、[後述](#not-supported)の `TypeError` でただちに失敗します。
- **アプリが提供するものだけ。** アプリがインクルードしていないルーター（テストからだけインポートされるものなど）は、アプリの起動時に何も接続しません。
- **インスタンス。** `inject()` と同様に、`Client` はコンテナごとに 1 インスタンス、`NotSingletonClient` はそれを宣言する引数ごとに 1 インスタンスです。リクエストごとではありません。
- **lifespan。** アプリ自身の `lifespan=` はその内側で実行されます。起動処理からは接続済みのクライアントが見え、終了処理はクライアントが切断される前に実行されます。終了時には、ワーカーと同じように、クライアントが切断される前に `Shutdown` がセットされ、アプリが `BackgroundTasks` を使っていればそれも停止されます。FastAPI 自身の `BackgroundTasks` は別のクラスで、クライアントではありません。
- **関数は関数のまま。** FastAPI から見たシグネチャは `Annotated[UserService, Depends(...)]` になりますが、単体テストなどでクライアントを渡して直接呼び出すことは、これまでどおりできます。

**テスト。** アプリをインポートしても何も構築されないので、テストでは `TestClient` がアプリを起動する前に、[`override()`](testing.md) か `global_di` フィクスチャでクライアントを差し替えます。

```python
# tests/test_api.py
from fastapi.testclient import TestClient

from app.api import app
from app.clients import Database
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_get_user() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/users/1").json() == "Hello, alice!"
        assert client.get("/me", headers={"X-User-Id": "7"}).json() == "alice"
```

```console
$ pytest -q tests/test_api.py
.                                                                        [100%]
1 passed in 0.16s
```

`app.dependency_overrides` も引き続き使えます。クライアントを受け取る依存関係の関数に対しても同様です。

**WebSocket。** WebSocket エンドポイントも、アプリ上でも `ClientRouter` 上でも、同じようにクライアントを受け取ります。

```python
# app/chat.py
from fastapi import FastAPI, WebSocket

from app.clients import UserService
from nuke_di.fastapi import setup

app = FastAPI()
setup(app)


@app.websocket("/greet")
async def greet(websocket: WebSocket, users: UserService) -> None:
    await websocket.accept()
    async for user_id in websocket.iter_text():
        await websocket.send_text(await users.greet(int(user_id)))
```

```python
# tests/test_chat.py
from fastapi.testclient import TestClient

from app.chat import app


def test_greet() -> None:
    with TestClient(app) as client, client.websocket_connect("/greet") as ws:
        ws.send_text("42")
        assert ws.receive_text() == "Hello, user-42!"
```

```console
$ pytest -q tests/test_chat.py
.                                                                        [100%]
1 passed in 0.16s
```

**接続に失敗したクライアント**があると、起動が失敗します。`SystemExit` ではサーバーのイベントループを突き抜けてしまうため、lifespan は `ConnectError` を原因とする通常の `RuntimeError` を送出し、サーバーはそれを報告して終了します。

```python
# app/broken.py
from fastapi import FastAPI

from nuke_di import Client
from nuke_di.fastapi import setup


class Kafka(Client):
    async def connect(self) -> None:
        raise OSError("broker kafka-1:9092 is unreachable")


app = FastAPI()
setup(app)


@app.post("/events")
async def publish(kafka: Kafka) -> None: ...
```

```console
$ uvicorn app.broken:app
INFO:     Started server process [81379]
INFO:     Waiting for application startup.
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
ERROR:    Traceback (most recent call last):
  ...
nuke_di.errors.ConnectError: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ...
RuntimeError: nuke-di clients failed to start: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable

ERROR:    Application startup failed. Exiting.
$ echo $?
3
```

## <a id="not-supported"></a>サポートされていないもの

次の場所ではクライアントを受け取れません。いずれも、ルートを宣言した時点で、その旨を示す `TypeError` が送出されます。

| 場所                                                    | 代わりの方法                                  |
|---------------------------------------------------------|-----------------------------------------------|
| `ClientRouter` / `ClientRoute` を使わずに作成したルーター | `ClientRouter(...)` で作成する              |
| `APIRouter(route_class=ClientRoute)` 上の WebSocket エンドポイント | `ClientRouter(...)` でルーターを作成する |
| オプショナルなクライアント `Database \| None`           | 普通の `Database`                             |
| エンドポイントや依存関係としての束縛メソッドや呼び出し可能オブジェクト | 関数またはクラス               |

これらとは異なり、`ClientRouter` ではなく素の `APIRouter` にインクルードされたルーターのルートは、古い FastAPI でしか検出されません。FastAPI 0.14x ではルートを宣言でき、アプリも起動しますが、そのルートへのリクエストは `RuntimeError: UserService was not started with the app: include the router of its route into
the app or into a ClientRouter, not into a plain APIRouter` で失敗します。

lifespan を経由せずに届いたリクエスト（`with` を使わない `TestClient(app)` など）は、`RuntimeError` になります：`UserService is not connected: start the app with its lifespan`。
