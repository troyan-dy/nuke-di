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
3. 起動時に lifespan が、アプリが提供するルート（アプリ自身のルートと、インクルードしたルーターのルート）のクライアントを解決し、それぞれの依存先の後に接続しました。終了時にはそれらを切断しました。
4. `/users/42` へのリクエストは接続済みの `UserService` を受け取りました。`/me` は依存関係 `current_user` を経由し、`current_user` も同じ方法で `db: Database` を受け取ります。

ルール：

- **クライアントが埋められる場所。** パスオペレーション、WebSocket エンドポイントと、それらが使うすべての依存関係の引数です。深さは問いません。関数のほか、`Depends(Auth)` や `Annotated[Auth, Depends()]` として使われるクラスも対象で、ルート、そのルーター、`include_router()`、アプリの `dependencies=` も含まれます。型ヒントがクライアントである引数はクライアントとして扱われ、`Depends` を伴わない `Annotated[UserService, ...]` の中にある場合も同様です。それ以外の引数（パス、クエリ、ヘッダー、ボディ、`Depends`）はすべて FastAPI が扱います。
- **ルーター。** ルーターは、`APIRouter` と同じ引数を受け取る `ClientRouter(...)` で作成し、アプリか別の `ClientRouter` にインクルードします。他のルーターをインクルードしないルーターであれば、`APIRouter(route_class=ClientRoute)` でも動作します。別のコンテナを使う場合は、`setup(app, container)` と `ClientRouter(container=container)` を使います。別のコンテナのルーターをインクルードすると、ただちに `TypeError` が送出されます。
- **`setup(app)` はルートより前に呼び出す。** それより前に宣言された、クライアントを受け取るルートは、[後述](#not-supported)の `TypeError` でただちに失敗します。
- **アプリが提供するものだけ。** アプリがインクルードしていないルーター（テストからだけインポートされるものなど）は、アプリの起動時に何も接続しません。
- **アプリごとに 1 つのコンテナ。** 各アプリは、その `setup()` に渡したコンテナのクライアントを受け取るので、テストなどで 2 つのコンテナ上の 2 つのアプリが同じ関数を同時に提供できます。`setup()` 済みのアプリにマウントされたアプリや、そのアプリのルートを提供するアプリ（たとえば `api` の lifespan を実行する `include_router(api.router)` 経由）は、そのアプリが起動したクライアントを受け取ります。
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

## <a id="class-based-views"></a>クラスベースビュー

必要なもの（リクエストのユーザーといくつかのクライアント）を共有するルートは、それを 1 つのクラスとして受け取ります。このクラスは型ヒント付きの `__init__` を持つ FastAPI の依存関係で、クライアントと同じように書きます。その `__init__` は、リクエストのデータとクライアントを並べて受け取ります。

```python
# app/views.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di.fastapi import setup

app = FastAPI()
setup(app)


class Account:
    # Built by FastAPI for every request, from a header and two clients
    def __init__(self, x_user_id: Annotated[int, Header()], db: Database, users: UserService) -> None:
        self.user_id = x_user_id
        self.db = db
        self.users = users

    async def name(self) -> str:
        return await self.db.fetch_user(self.user_id)

    async def greeting(self) -> str:
        return await self.users.greet(self.user_id)


CurrentAccount = Annotated[Account, Depends()]


@app.get("/me")
async def me(account: CurrentAccount) -> str:
    return await account.name()


@app.get("/me/greeting")
async def greeting(account: CurrentAccount) -> str:
    return await account.greeting()
```

```console
$ uvicorn app.views:app
INFO:     Started server process [50908]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51798 - "GET /me HTTP/1.1" 200 OK
INFO:     127.0.0.1:51800 - "GET /me/greeting HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [50908]
```

```console
$ curl localhost:8000/me -H "X-User-Id: 7"
"user-7"
$ curl localhost:8000/me/greeting -H "X-User-Id: 7"
"Hello, user-7!"
```

テストでは、このクラスを使うすべてのルートに対して、クライアントを一度差し替えるだけで済みます。

```python
# tests/test_views.py
from fastapi.testclient import TestClient

from app.clients import Database
from app.views import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_account() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/me", headers={"X-User-Id": "7"}).json() == "alice"
        assert client.get("/me/greeting", headers={"X-User-Id": "7"}).json() == "Hello, alice!"
```

```console
$ pytest -q tests/test_views.py
.                                                                        [100%]
1 passed in 0.18s
```

ルール：

- **ビューはリクエストごとに 1 つ、クライアントはコンテナごとに 1 つ。** FastAPI はリクエストごとに `Account` を構築しますが、その中の `db` と `users` はコンテナの接続済みクライアントで、どのリクエストでも同じオブジェクトです。nuke-di は依存関係の関数と同じようにクラスのシグネチャを書き換えましたが、`__init__` には手を加えていません。単体テストで `Account(x_user_id=7, db=db, users=users)` と呼び出すことは、これまでどおりできます。dataclass も同じように動作し、そのフィールドが引数になります。
- **リクエストから何も必要としないビューはクライアント。** `class Account(Client)` を `account: Account` として受け取ると、コンテナごとに一度だけ構築され、他のクライアントと一緒に接続します。一方、クライアントクラスを `Annotated[Account, Depends()]` と書くと（たとえば `@client_dataclass` でデコレートしたもの）、代わりに FastAPI がリクエストごとに構築し、その `connect()` は一度も実行されません。`Depends()` は付けないでください。
- **fastapi-utils の `@cbv` は不要。** `@cbv` はルートを自前の素の `APIRouter` 上で宣言し直すため、クライアント型のクラス属性は `include_router()` で `TypeError: Database is a nuke-di client, not a pydantic type` となって失敗します。上のクラスは、FastAPI の機能だけでルート間にクライアントを共有します。

## <a id="an-app-per-test-container"></a>テストのコンテナごとにアプリを作る

アプリファクトリーは渡されたコンテナを使ってアプリを構築するので、各テストは専用のコンテナで、サーバーはグローバルな `DI` で動きます。関数はモジュールレベルに置いたままです。

```python
# app/factory.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di import DI, Dependencies
from nuke_di.fastapi import ClientRouter, setup


async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user


def make_app(container: Dependencies) -> FastAPI:
    app = FastAPI()
    setup(app, container)
    app.add_api_route("/users/{user_id}", get_user)

    # A router fills clients from one container, so every app creates its own
    account = ClientRouter(prefix="/me", container=container)
    account.add_api_route("", me)
    app.include_router(account)
    return app


def create_app() -> FastAPI:
    # For the server: `uvicorn --factory app.factory:create_app`
    return make_app(DI)
```

```console
$ uvicorn --factory app.factory:create_app
INFO:     Started server process [50968]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51815 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51817 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [50968]
```

テストは `di` フィクスチャを使ってアプリを構築します。

```python
# tests/test_factory.py
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.clients import Database
from app.factory import current_user, make_app
from nuke_di import Dependencies


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


@pytest.fixture
def app(di: Dependencies) -> Iterator[FastAPI]:
    # `di` is a fresh container for every test, a fixture of nuke-di
    with di.override(Database, FakeDatabase()):
        yield make_app(di)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_get_user(client: TestClient) -> None:
    assert client.get("/users/1").json() == "Hello, alice!"


def test_me(client: TestClient) -> None:
    assert client.get("/me", headers={"X-User-Id": "7"}).json() == "alice"


def test_dependency_overrides(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[current_user] = lambda: "carol"
    assert client.get("/me").json() == "carol"
```

```console
$ pytest -q tests/test_factory.py
...                                                                      [100%]
3 passed in 0.16s
```

ルール：

- **アプリ全体で `override()` は 1 回。** `di.override(Database, FakeDatabase())` は、依存関係 `current_user`、`UserService`、そのほか `Database` を受け取るすべてのものに対してデータベースを差し替えます。FastAPI だけで同じことをするには、依存関係の関数ごとに `app.dependency_overrides` のエントリーが必要です。
- **複数のコンテナで使われる関数。** `get_user` と `current_user` が書き換えられるのは 1 回だけです。各アプリは起動時にそれらのクライアントを自分のコンテナで解決し、リクエストは届いた先のアプリのクライアントを受け取ります。異なるコンテナで構築したアプリは、順番にでも同時にでも、同じ関数を提供できます。
- **ルーターはアプリごとに。** `ClientRouter` は 1 つのコンテナからクライアントを埋めます。別のコンテナのルーターをインクルードしたアプリは `TypeError: the router fills clients from another container than this app` を送出します。ルーターはファクトリーの中で作成してください。
- **`app.dependency_overrides`** は 1 つのアプリに属し、引き続き使えます。上の `current_user` のように、クライアントを受け取る依存関係に対しても同様です。

## <a id="strawberry-graphql"></a>Strawberry GraphQL

Strawberry の FastAPI ルーター `GraphQLRouter` は `APIRouter` であり、そのルートは FastAPI の依存関係で GraphQL のコンテキストを構築します。そこで、コンテキストをクライアントを受け取る型ヒント付きの `__init__` を持つクラスにし、リゾルバーは `info.context` からクライアントを読み取ります。

```bash
pip install "nuke-di[fastapi]" strawberry-graphql
```

```python
# app/graphql.py
from collections.abc import AsyncIterator

import strawberry
from fastapi import FastAPI
from strawberry.fastapi import BaseContext, GraphQLRouter

from app.clients import UserService
from nuke_di.fastapi import ClientRoute, setup


class Context(BaseContext):
    # Built by FastAPI for every request, as a dependency of Strawberry's routes
    def __init__(self, users: UserService) -> None:
        super().__init__()
        self.users = users


@strawberry.type
class Query:
    @strawberry.field
    async def greeting(self, info: strawberry.Info[Context], user_id: int) -> str:
        return await info.context.users.greet(user_id)


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def greetings(self, info: strawberry.Info[Context], user_ids: list[int]) -> AsyncIterator[str]:
        for user_id in user_ids:
            yield await info.context.users.greet(user_id)


schema = strawberry.Schema(query=Query, subscription=Subscription)

app = FastAPI()
setup(app)
graphql = GraphQLRouter(schema, context_getter=Context, route_class=ClientRoute)
app.include_router(graphql, prefix="/graphql")
```

```console
$ uvicorn app.graphql:app
INFO:     Started server process [61129]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51979 - "POST /graphql HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [61129]
```

```console
$ curl localhost:8000/graphql -H 'Content-Type: application/json' -d '{"query": "{ greeting(userId: 42) }"}'
{"data":{"greeting":"Hello, user-42!"}}
```

クエリーと、同じルーターの WebSocket 上のサブスクリプション：

```python
# tests/test_graphql.py
from fastapi.testclient import TestClient

from app.clients import Database
from app.graphql import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_query() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        response = client.post("/graphql", json={"query": "{ greeting(userId: 1) }"})

    assert response.json() == {"data": {"greeting": "Hello, alice!"}}


def test_subscription() -> None:
    query = "subscription { greetings(userIds: [1, 2]) }"
    with (
        TestClient(app) as client,
        client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as ws,
    ):
        ws.send_json({"type": "connection_init"})
        assert ws.receive_json() == {"type": "connection_ack"}
        ws.send_json({"id": "1", "type": "subscribe", "payload": {"query": query}})
        assert ws.receive_json()["payload"] == {"data": {"greetings": "Hello, user-1!"}}
        assert ws.receive_json()["payload"] == {"data": {"greetings": "Hello, user-2!"}}
        assert ws.receive_json() == {"id": "1", "type": "complete"}
```

```console
$ pytest -q tests/test_graphql.py
..                                                                       [100%]
2 passed in 0.21s
```

ルール：

- **コンテキストがクライアントを受け取り、リゾルバーがコンテキストを受け取る。** FastAPI はリクエストと WebSocket 接続のたびに `Context` を構築し、そこにコンテナの接続済みクライアントを入れます。`strawberry.Info[Context]` はリゾルバーにその型を伝えます。リゾルバーは型ヒントでクライアントを受け取りません。Strawberry には独自の依存性注入がなく、値を下に渡す手段は `info.context` です。
- **`route_class=ClientRoute`** を指定すると、ほかの `APIRouter` と同じように、ルーターのルートがクライアントを埋めます。Strawberry はコンテキストゲッターを独自の依存関係でラップして FastAPI に渡しますが、nuke-di はそれをたどって `Context` まで到達します。
- **サブスクリプション。** FastAPI は、`APIRouter` 上のほかの WebSocket と同じく、ルーターの WebSocket ルートをルートクラスなしで構築します。それでもこのルートはクライアント入りの `Context` を受け取ります。Strawberry のコンテキストの依存関係を GET と POST のルートと共有しており、ルーターはそれらのルートを先に `ClientRoute` で宣言するからです。それらのルートが `Context` を書き換え、そのクライアントをアプリの起動処理に組み込みます。このようなルーターに自分で追加した WebSocket エンドポイントは、引き続き `TypeError` を送出します。[サポートされていないもの](#not-supported) を参照してください。
- **別のコンテナ**：`setup(app, container)` の後に `route_class=app.router.route_class`。
- Strawberry の Litestar コントローラーは `ClientPlugin` を通じてクライアントを受け取ります。[Litestar](litestar.md#strawberry-graphql) を参照してください。
