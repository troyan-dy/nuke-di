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

## <a id="differences-from-fastapi"></a>FastAPI との違い

ハンドラーの書き方はどちらでも同じで、`users: UserService` です。違いは、それぞれのフレームワークの注入の仕組みから生じます。FastAPI はすべての関数のシグネチャにある `Depends` を読み取り、Litestar は依存関係を引数名で照合します。そのため `ClientPlugin` は、すべてのクライアントをその引数名でアプリに提供します（[ADR-0004](../../adr/0004-litestar-clients-by-name.md)）。

| | FastAPI | Litestar |
|---|---|---|
| セットアップ | ルートより前に `setup(app)`。ルーターは `ClientRouter` で作成する | `plugins=` に `ClientPlugin()`。ルーターやコントローラーは何でもよい |
| 検出されるハンドラー | `setup(app)` の後に、アプリまたはインクルードした `ClientRouter` や `APIRouter(route_class=ClientRoute)` で宣言されたすべてのルート | アプリの作成時に渡されたハンドラー。後から `app.register()` で追加したものは検出されない |
| 引数名 | 自由：こちらで `users: UserService`、あちらで `users: Billing` としてよい | アプリ全体で 1 つの名前に 1 つのクライアント。1 つの名前に 2 つのクライアントがあると、アプリの作成時に `TypeError` が送出される |
| `NotSingletonClient`（削除予定、[ADR-0006](../../adr/0006-clients-live-as-long-as-the-container.md)） | 引数ごとに 1 インスタンス | 引数名ごとに 1 インスタンスで、その名前を使うすべてのハンドラーが共有する |
| 関数で変わるもの | `__signature__`。`get_type_hints()` は引き続き `UserService` を返す | `__annotations__`。`get_type_hints(include_extras=True)` は `Annotated[UserService, Dependency(), SkipValidationMarker()]` を返す |
| WebSocket | `@app.websocket` エンドポイント | `@websocket` ハンドラー。WebSocket リスナーは `TypeError` を送出する |
| アプリ自身の lifespan | その内側で実行される：クライアントはその前に接続し、その後に切断する | クライアントは `lifespan=` と `on_startup=` より前に接続し、`on_shutdown=` の後に切断する |
| テストでの依存関係の差し替え | `override()` または `app.dependency_overrides` | `override()`、またはいずれかのレイヤーで宣言した同じ名前の依存関係（クライアントより優先される） |
| 別のコンテナ | `setup(app, container)` と `ClientRouter(container=container)` | `ClientPlugin(container)` |

## <a id="litestar-3"></a>Litestar 3

Litestar 3 はまだリリースされていません。2026-10-10 時点で PyPI の最新リリースは 2026-06-11 の 2.24.0 で、3.0 のプレリリースもありません。わかっていることは 2 つで、nuke-di はリリースされるまで何も変更しません。

- **推論による依存関係は廃止される。** Litestar 2.24 は、名前だけで照合される依存関係について `Inferred dependencies will stop working in Litestar 3.0` と警告します。nuke-di が書き込むアノテーション `Annotated[UserService, Dependency(), SkipValidationMarker()]` は、`NamedDependency[...]` と `SkipValidation[...]` が表すもの、つまり明示的な形であり、Litestar 2.24 はそのどれについても警告しません。
- **型による注入が計画されている。** 2026-07-26 の [v3 の告知](https://litestar.dev/blog/v3-announcement) では、`NamedDependency` と並んで `TypeDependency[SomeService]` を導入し、プロバイダーを型をキーにして `dependencies={SomeService: provide_some_service}` のように登録する計画が示されています。また、この DI の刷新が、3.0 がまだベータに進めない理由となっている機能として挙げられています。

プロバイダーを型で登録できるようになれば、`ClientPlugin` はすべてのクライアントを引数名ではなくそのクラスで提供できるようになり、上の表のうち名前とインスタンスに関する行はなくなります。そうするかどうかは、3.0 とその API が公開された時点で ADR-0004 を見直して決めます。どちらにしてもハンドラーは変わりません。ハンドラーは `users: UserService` と宣言するだけで、それを Litestar にどう伝えるかはプラグインだけが決めます。

## <a id="strawberry-graphql"></a>Strawberry GraphQL

Strawberry の Litestar コントローラーは Litestar の依存関係で GraphQL のコンテキストを構築するので、コンテキストゲッターは他の依存関係と同じようにクライアントを受け取り、リゾルバーは `info.context` からそれを読み取ります。

```bash
pip install "nuke-di[litestar]" strawberry-graphql
```

```python
# app/litestar_graphql.py
import strawberry
from litestar import Litestar
from strawberry.litestar import BaseContext, make_graphql_controller

from app.clients import UserService
from nuke_di.litestar import ClientPlugin


class Context(BaseContext, kw_only=True):
    users: UserService


async def get_context(users: UserService) -> Context:
    return Context(users=users)


@strawberry.type
class Query:
    @strawberry.field
    async def greeting(self, info: strawberry.Info[Context], user_id: int) -> str:
        return await info.context.users.greet(user_id)


schema = strawberry.Schema(query=Query)
GraphQLController = make_graphql_controller(schema, path="/graphql", context_getter=get_context)
app = Litestar([GraphQLController], plugins=[ClientPlugin()])
```

```console
$ uvicorn app.litestar_graphql:app
INFO:     Started server process [56803]
INFO:     Waiting for application startup.
database: connected
INFO - 2026-10-10 18:40:33,849 - nuke_di.core - core - Connected 2 clients in 0.00s (slowest: Database 0.00s, UserService 0.00s)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:53760 - "POST /graphql HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [56803]
```

```console
$ curl localhost:8000/graphql -H 'Content-Type: application/json' -d '{"query": "{ greeting(userId: 42) }"}'
{"data":{"greeting":"Hello, user-42!"}}
```

```python
# tests/test_litestar_graphql.py
from litestar.testing import TestClient

from app.clients import Database
from app.litestar_graphql import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_query() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        response = client.post("/graphql", json={"query": "{ greeting(userId: 1) }"})

    assert response.json() == {"data": {"greeting": "Hello, alice!"}}
```

```console
$ pytest -q -W ignore::DeprecationWarning tests/test_litestar_graphql.py
.                                                                        [100%]
1 passed in 0.37s
```

ルール：

- **コンテキストゲッターは関数にする。** Strawberry の Litestar 用 `BaseContext` は msgspec の `Struct` です。クラスそのものを `context_getter=Context` として渡すと、すべてのリクエストが `msgspec.ValidationError` で失敗します。
- **サブスクリプション**は同じコントローラーの WebSocket ハンドラーで実行されます。`ClientPlugin` はこのハンドラーも検出し、コンテキストは同じゲッターから渡されます。
- **非推奨の警告は Strawberry によるもの。** Litestar 2.24 では、Strawberry 0.332 のコントローラーが自身の依存関係 `custom_context`、`context`、`context_ws`、`root_value`、`response` を名前だけで宣言しており、Litestar はそれぞれについて警告を出します。テストを `-W ignore::DeprecationWarning` 付きで実行しているのはそのためです。`get_context` の引数 `users` は警告を出しません。
- ほかに必要なものはありません。Litestar はコントローラーの依存関係をそのまま `ClientPlugin` に渡します。FastAPI ではルーターに `route_class=ClientRoute` を指定します。[FastAPI](fastapi.md#strawberry-graphql) を参照してください。
