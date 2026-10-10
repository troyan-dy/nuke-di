# <a id="starlette-quart-and-any-asgi-app"></a>Starlette、Quart、その他あらゆる ASGI アプリ

[English](../../guide/asgi.md) · [Русский](../ru/asgi.md) · [简体中文](../zh-CN/asgi.md) · [Español](../es/asgi.md) · [Português (Brasil)](../pt-BR/asgi.md) · **日本語** · [Polski](../pl/asgi.md)

← [ドキュメント](../README.ja.md#documentation)

Starlette や Quart のように依存性注入を持たないフレームワークでは、ハンドラーの引数を型ヒントで埋めることができません。残りは `nuke_di.asgi.lifespan()` が引き受けます。これはアプリの lifespan そのもので、アプリの起動時に列挙されたクライアントを接続し、停止時に切断します。ハンドラーは `get()` でそこからクライアントを受け取ります。フレームワークは一切インポートせず、extra も不要です。

```bash
pip install nuke-di
```

- [Starlette](#starlette)
- [アプリ自身の lifespan](#the-apps-own-lifespan)
- [テスト](#testing)
- [Quart](#quart)
- [aiohttp](#aiohttp)
- [素の ASGI アプリ](#a-plain-asgi-app)
- [エラー](#errors)

## <a id="starlette"></a>Starlette

[FastAPI](fastapi.md) の例と同じクライアントを使います。

```python
# app/starlette_api.py
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.clients import Database, UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

# The clients the handlers take: they connect, with their dependencies, when the app starts
clients = lifespan(DI, UserService, Database)


async def get_user(request: Request) -> PlainTextResponse:
    users = clients.get(UserService)
    return PlainTextResponse(await users.greet(request.path_params["user_id"]))


async def me(request: Request) -> PlainTextResponse:
    db = clients.get(Database)
    return PlainTextResponse(await db.fetch_user(int(request.headers["X-User-Id"])))


app = Starlette(
    routes=[Route("/users/{user_id:int}", get_user), Route("/me", me)],
    lifespan=clients,
)
```

```console
$ uvicorn app.starlette_api:app
INFO:     Started server process [47010]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:50476 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:50478 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [47010]
```

```console
$ curl localhost:8000/users/42
Hello, user-42!
$ curl localhost:8000/me -H "X-User-Id: 7"
user-7
```

ルール：

- **リストは明示的。** クライアントを探すためのルートテーブルがないので、`lifespan(container, *clients)` にはハンドラーが受け取るクライアントをすべて書き出します。`Database` は `UserService` の依存先としてどのみち接続されますが、`clients.get(Database)` が動くのは `Database` を列挙したときだけです。そこに頼ったハンドラーは、`UserService` が `Database` に依存しなくなった日に壊れてしまいます。
- **`get()` には型が付く。** `request.state` の属性とは違い、`clients.get(UserService)` は mypy や pyright から見ても `UserService` を返します。普通のメソッドなので、ハンドラー、WebSocket エンドポイント、ミドルウェア、バックグラウンドタスクのどこでも使えます。`clients` を持つモジュールは、他のモジュールと同じようにハンドラーからインポートされます。
- **起動時に解決される。** `clients` を作成した時点では何も解決されません。起動のたびに列挙されたクライアントが改めて解決されるので、アプリの起動前に `override()` すればそのクライアントを差し替えられ、2 回目の起動では新しいクライアントが得られます。
- **終了処理。** 終了時には `Shutdown` がセットされ、`BackgroundTasks` が停止され、それからクライアントが切断されます。順序は[ワーカー](workers-and-jobs.md)と同じです。
- **アプリは一度に 1 つ。** コンテナは一度しか接続されません。最初のアプリが動いている間に同じコンテナで 2 つ目のアプリを起動すると、その起動は失敗します。
- **FastAPI でも使える。** ルートのシグネチャを書いたとおりに保つ FastAPI アプリなら、同じように `FastAPI(lifespan=clients)` を渡せます。`nuke_di.fastapi.setup()` を使う場合は、代わりにルートが型ヒントでクライアントを受け取ります。[FastAPI](fastapi.md) を参照してください。

## <a id="the-apps-own-lifespan"></a>アプリ自身の lifespan

`clients(app)` は非同期コンテキストマネージャーなので、アプリ自身の lifespan はまずそこに入り、その内側で、クライアントが接続された状態のまま自分の起動処理と終了処理を実行します。yield する値は、いつもどおりアプリの state です。

```python
# app/own_lifespan.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.clients import UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService)


@asynccontextmanager
async def app_lifespan(app: Starlette) -> AsyncIterator[dict[str, str]]:
    async with clients(app):
        # The app's own startup and shutdown code runs with the clients connected
        greeting = await clients.get(UserService).greet(0)
        print("warmed up:", greeting)
        yield {"greeting": greeting}
        print("app: stopping")


async def index(request: Request) -> PlainTextResponse:
    return PlainTextResponse(request.state.greeting)


app = Starlette(routes=[Route("/", index)], lifespan=app_lifespan)
```

```console
$ uvicorn app.own_lifespan:app
INFO:     Started server process [46993]
INFO:     Waiting for application startup.
database: connected
warmed up: Hello, user-0!
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:50465 - "GET / HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
app: stopping
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [46993]
```

## <a id="testing"></a>テスト

テストでは、`TestClient` がアプリを起動する前にクライアントを差し替えます。

```python
# tests/test_starlette_api.py
from starlette.testclient import TestClient

from app.clients import Database
from app.starlette_api import app
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
$ pytest -q tests/test_starlette_api.py
.                                                                        [100%]
1 passed in 0.04s
```

`with` を使わない `TestClient(app)` は lifespan を実行せずにリクエストを送るので、ハンドラーがそれを知らせます。

```console
$ python -c "from starlette.testclient import TestClient; from app.starlette_api import app; TestClient(app).get('/users/1')"
Traceback (most recent call last):
  ...
RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)`
```

## <a id="quart"></a>Quart

Quart には `lifespan=` 引数がありません。起動時に `before_serving` フックを、終了時に `after_serving` フックを実行します。その間、1 つの `AsyncExitStack` がクライアントを接続したまま保ちます。

```python
# app/quart_api.py
from contextlib import AsyncExitStack

from quart import Quart, request

from app.clients import Database, UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService, Database)
app = Quart(__name__)
running = AsyncExitStack()


@app.before_serving
async def connect() -> None:
    await running.enter_async_context(clients(app))


@app.after_serving
async def disconnect() -> None:
    await running.aclose()


@app.get("/users/<int:user_id>")
async def get_user(user_id: int) -> str:
    return await clients.get(UserService).greet(user_id)


@app.get("/me")
async def me() -> str:
    return await clients.get(Database).fetch_user(int(request.headers["X-User-Id"]))
```

```console
$ uvicorn app.quart_api:app
INFO:     Started server process [44067]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:49372 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:49374 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [44067]
```

テストでは `test_app()` でアプリを起動します。

```python
# tests/test_quart_api.py
from app.clients import Database
from app.quart_api import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_get_user() -> None:
    with DI.override(Database, FakeDatabase()):
        async with app.test_app() as test_app:
            response = await test_app.test_client().get("/users/1")
            assert await response.get_data(as_text=True) == "Hello, alice!"
```

```console
$ pytest -q tests/test_quart_api.py
.                                                                        [100%]
1 passed in 0.15s
```

Quart は同じ種類のフックを登録された順に呼び出します。`connect` は自分で書いた `before_serving` フックより前に来るので、それらのフックからクライアントが見えます。`disconnect` は自分で書いた `after_serving` フックの後に来ます。Quart の `while_serving` を使えばもっと短く書けますが、これは登録時にジェネレーターを一度だけ作るため、アプリはプロセスごとに一度しか起動できず、アプリを起動する 2 つ目のテストが失敗します。

## <a id="aiohttp"></a>aiohttp

aiohttp 3.14 は `cleanup_ctx` に非同期コンテキストマネージャーを受け付け、`clients` はまさにそれです。

```python
# app/aiohttp_api.py
from aiohttp import web

from app.clients import Database, UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService, Database)


async def get_user(request: web.Request) -> web.Response:
    users = clients.get(UserService)
    return web.Response(text=await users.greet(int(request.match_info["user_id"])))


app = web.Application()
app.router.add_get("/users/{user_id}", get_user)
app.cleanup_ctx.append(clients)  # aiohttp 3.14 or newer

if __name__ == "__main__":
    web.run_app(app)
```

```console
$ python -m app.aiohttp_api
database: connected
======== Running on http://0.0.0.0:8080 ========
(Press CTRL+C to quit)
^C
database: disconnected
```

```console
$ curl localhost:8080/users/42
Hello, user-42!
```

古い aiohttp では、代わりに非同期ジェネレーターを渡します。

```python
from collections.abc import AsyncIterator


async def run_clients(app: web.Application) -> AsyncIterator[None]:
    async with clients(app):
        yield


app.cleanup_ctx.append(run_clients)
```

## <a id="a-plain-asgi-app"></a>素の ASGI アプリ

フレームワークを使わない ASGI アプリは、サーバーから届く lifespan メッセージに自分で応答します。その場合、`clients()` は引数を取りません。

```python
# app/raw_asgi.py
from typing import Any

from app.clients import UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService)


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        await receive()  # lifespan.startup
        started = False
        try:
            async with clients():
                await send({"type": "lifespan.startup.complete"})
                started = True
                await receive()  # lifespan.shutdown
        except Exception as exc:
            await send({"type": f"lifespan.{'shutdown' if started else 'startup'}.failed", "message": str(exc)})
            raise
        await send({"type": "lifespan.shutdown.complete"})
        return

    body = (await clients.get(UserService).greet(42)).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})
```

```console
$ uvicorn app.raw_asgi:app
INFO:     Started server process [48646]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:50998 - "GET / HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [48646]
```

## <a id="errors"></a>エラー

| 状況 | 送出される例外 |
|---|---|
| アプリの lifespan なしで、または停止後にハンドラーが実行された | ``RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` `` |
| リストにないクライアントを `get()` した | ``RuntimeError: Database is not a client of this lifespan: list it in `lifespan(container, ...)` `` |
| クライアントの `connect()` が失敗した | `RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused`。サーバーは起動の失敗を報告して終了し、コンテナはフラッシュされた状態になる |
| コンテナがすでに接続されている（別のアプリによってなど） | `RuntimeError: nuke-di clients failed to start: the container is already connected` |
| `lifespan(Database)` でコンテナを渡し忘れた | `TypeError: lifespan() takes the container first, then the clients, e.g. lifespan(DI, Database); got <class 'app.clients.Database'>` |
| `lifespan(DI, "Database")` でクライアントクラスでないものを渡した | `TypeError: 'Database' is not a client: subclass Client or NotSingletonClient` |

uvicorn の下で、`connect()` が `OSError("connection refused")` を送出する `Database` を使った場合：

```console
$ uvicorn app.broken_api:app
INFO:     Started server process [44319]
INFO:     Waiting for application startup.
Database.connect() raised OSError: connection refused
...
RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused

ERROR:    Application startup failed. Exiting.
```
