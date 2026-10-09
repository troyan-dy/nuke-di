# <a id="litestar"></a>Litestar

[English](../../guide/litestar.md) · [Русский](../ru/litestar.md) · [简体中文](../zh-CN/litestar.md) · [Español](../es/litestar.md) · [Português (Brasil)](../pt-BR/litestar.md) · **日本語** · [Polski](../pl/litestar.md)

← [ドキュメント](../README.ja.md#documentation)

Litestar のルートハンドラーも、プラグインを通じて型ヒントでクライアントを受け取ります。

```bash
pip install "nuke-di[litestar]"
```

Litestar 2.15 以降が必要です。[FastAPI](fastapi.md) の例と同じクライアントを使います。

```python
# app/litestar_api.py
from typing import Annotated

from litestar import Litestar, get
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, HeaderParameter

from app.clients import Database, UserService
from nuke_di.litestar import ClientPlugin


@get("/users/{user_id:int}")
async def get_user(user_id: FromPath[int], users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, HeaderParameter(name="X-User-Id")], db: Database) -> str:
    return await db.fetch_user(x_user_id)


@get("/me", dependencies={"user": Provide(current_user)})
async def me(user: NamedDependency[str]) -> str:
    return user


app = Litestar([get_user, me], plugins=[ClientPlugin()])
```

```console
$ uvicorn app.litestar_api:app
INFO:     Started server process [6801]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51940 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51942 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [6801]
```

```console
$ curl localhost:8000/users/42
Hello, user-42!
$ curl localhost:8000/me -H "X-User-Id: 7"
user-7
```

`ClientPlugin()` は `get_user` の `users: UserService` と、依存関係 `current_user` の `db: Database` を見つけ、その両方を依存関係として Litestar に提供し、起動時に接続しました。

ルール：

- **クライアントが埋められる場所。** アプリの作成時に渡された HTTP ハンドラーと `@websocket` ハンドラー（任意の深さのルーターやコントローラーのものも含む）の引数と、アプリ、ルーター、コントローラー、ハンドラーで宣言されたすべての依存関係（関数とクラス）の引数です。
- **名前で。** Litestar は依存関係を引数名で提供するので、nuke-di はすべてのクライアント引数をその名前でアプリに提供します。ひとつの名前はアプリ全体でひとつのクライアントを意味します。あるハンドラーで `users: UserService`、別のハンドラーで `users: Billing` とすると、アプリの作成時に `TypeError` が送出されます。アプリ、ルーター、コントローラー、ハンドラーで同じ名前の依存関係が宣言されている場合は、クライアントよりもそちらが優先されます。
- **インスタンス。** `Client` はコンテナごとに 1 インスタンス、`NotSingletonClient` は引数名ごとに 1 インスタンスです。
- **lifespan。** クライアントは、アプリ自身の `lifespan=` と `on_startup=` が実行される前に接続し、Litestar が最後に呼び出す `on_shutdown=` フックの後に切断します。`Shutdown` と `BackgroundTasks` は [FastAPI](fastapi.md) と同じように動作します。
- **関数は関数のまま。** クライアント引数は、値が検証されない Litestar の明示的な依存関係として `Annotated[UserService, Dependency(), SkipValidationMarker()]` と注釈されます。これは、名前だけで照合される依存関係の代わりに Litestar 2.23 が求める形です。関数を直接呼び出すことは、これまでどおりできます。
- **プラグイン。** ルートハンドラーを追加するプラグインがある場合は、`ClientPlugin()` をそれらの後に置いてください。`ClientPlugin()` は、自分の番が来た時点でアプリが持っているハンドラーを参照します。
- **別のコンテナ。** `ClientPlugin(container)` を使います。

**テスト。** FastAPI と同様に、テストでは `TestClient` がアプリを起動する前にクライアントを差し替えます。

```python
# tests/test_litestar_api.py
from litestar.testing import TestClient

from app.clients import Database
from app.litestar_api import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_get_user() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/users/1").text == "Hello, alice!"
        assert client.get("/me", headers={"X-User-Id": "7"}).text == "alice"
```

```console
$ pytest -q tests/test_litestar_api.py
.                                                                        [100%]
1 passed in 0.23s
```

**サポートされていないもの。** WebSocket リスナー（`@websocket_listener` や `WebsocketListener` クラス）はクライアントを受け取れません。Litestar はリスナーが宣言された時点、つまりプラグインがそれを見る前にシグネチャを読み取るためです。そのためアプリは `TypeError` を送出し、代わりに `@websocket` ハンドラーを使うよう示します。Litestar が予約している名前（`state` や `request` など）のクライアント引数でも `TypeError` が送出されます。アプリの作成後に `app.register()` で登録されたハンドラーは検出されません。
