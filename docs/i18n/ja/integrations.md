# <a id="writing-an-integration"></a>インテグレーションを書く

[English](../../guide/integrations.md) · [Русский](../ru/integrations.md) · [简体中文](../zh-CN/integrations.md) · [Español](../es/integrations.md) · [Português (Brasil)](../pt-BR/integrations.md) · **日本語** · [Polski](../pl/integrations.md)

← [ドキュメント](../README.ja.md#documentation)

フレームワークとのインテグレーションが行うことは 2 つです。フレームワークのハンドラーが、そのフレームワーク自身の依存性注入を通じて型ヒントでクライアントを受け取ること、そしてアプリの起動時にコンテナが接続し、停止時に切断することです。[FastAPI](fastapi.md)、[Litestar](litestar.md)、[FastStream](faststream.md) のインテグレーションは `nuke_di.integration` の上に作られており、ほかのフレームワークとのインテグレーションが nuke-di から必要とするのは、これと公開 API だけです。grpc.aio、aiohttp、websockets、APScheduler、Textual、Temporal のワーカーのように独自の依存性注入を持たないサーバーには、インテグレーションはまったく要りません。`@worker` の中で動かせばよく、詳しくは[ワーカーの中で動くサーバー](servers-in-workers.md)を参照してください。

| 名前 | 役割 |
|---|---|
| `Framework(name, not_started, not_connected)` | クライアントが見つからないときに、フレームワークの利用者に伝えるメッセージ。メッセージ中の `{client}` はクライアントのクラス名になります。 |
| `DependsFramework(..., depends, make_depends, per_container=True)` | `Depends(...)` マーカーで注入するフレームワーク。`depends` はそのマーカーのクラス、`make_depends` は関数からマーカーを作る関数です。 |
| `bind(call, container, framework)` | ハンドラー、依存関係の関数、依存関係のクラスのシグネチャと、それらが使う依存関係のシグネチャを書き換えます。クライアントの引数はそれぞれ `Annotated[Client, Depends(...)]` になります。クライアントごとの `Binding` を返します。 |
| `Binding` | クライアントの引数 1 つ。`get()` は起動時に解決されたクライアントを返すか、`not_started` / `not_connected` を送出します。 |
| `running(container, bindings)` | 非同期コンテキストマネージャー。`bindings` のクライアントを解決してコンテナを接続し、終了時に `Shutdown` をセットして `BackgroundTasks` を止め、切断します。`ConnectError` と `InitializeDependencyError` は `RuntimeError` になり、サーバーはそれを起動の失敗として報告します。循環などクライアントツリーのエラーはそのまま伝わります。 |
| `wrap_lifespan(original, container, bindings)` | アプリ自身の lifespan `original` を `running()` の内側で実行する lifespan。`bindings` は起動時に呼ばれるので、`setup()` の後に宣言したハンドラーも見つかります。 |
| `client_of(hint, *markers)` | 型ヒントが求めるクライアント、または `None`。対象は `Client`、および `markers` のいずれも含まない `Annotated[Client, ...]` です。 |
| `unique(bindings)` | 重複を除いた `bindings`。1 つの依存関係が複数のハンドラーから到達できることはよくあります。 |

## <a id="a-framework-with-depends"></a>`Depends` のあるフレームワーク

FastAPI と FastStream（fast-depends 経由）は、ハンドラーの `inspect.signature()` を読み、各 `Depends(...)` マーカーの依存関係を呼び出します。マーカーが同じように動作するフレームワークならどれも同じです。こうしたフレームワークでは、`bind()` が `users: UserService` を `users: Annotated[UserService, Depends(binding.get)]` に置き換え、残りはフレームワークが行います。公開されたキットだけで書いた FastStream のインテグレーションは、次のモジュールです。

```python
# myapp/faststream_di.py
from typing import Any

from faststream import Depends, FastStream

from nuke_di import DI, Dependencies
from nuke_di.integration import Binding, DependsFramework, bind, unique, wrap_lifespan


def _noop() -> None: ...


FRAMEWORK = DependsFramework(
    name="FastStream",
    # The class of FastStream's markers, and the function that builds one
    depends=type(Depends(_noop)),
    make_depends=Depends,
    not_started="{client} was not started with the app: declare its subscriber before the app starts",
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`",
    # FastStream builds a subscriber on every start: a function is bound once, whatever the container
    per_container=False,
)


def setup(app: FastStream, container: Dependencies = DI) -> None:
    # Connect the container around the app's own lifespan, with the clients found on startup
    app.lifespan_context = wrap_lifespan(app.lifespan_context, container, lambda: _bindings(app, container))
    for broker in app.brokers:
        # Rewrite a subscriber's function whenever FastStream builds the subscriber
        config = broker.config.fd_config
        config.call_decorators = (*config.call_decorators, _Rewrite(container))


class _Rewrite:
    def __init__(self, container: Dependencies) -> None:
        self.container = container

    def __call__(self, call: Any) -> Any:
        bind(call, self.container, FRAMEWORK)
        return call


def _bindings(app: FastStream, container: Dependencies) -> list[Binding]:
    # The clients of every subscriber and of the dependencies it declares, each once
    bindings: list[Binding] = []
    for broker in app.brokers:
        for subscriber in broker.subscribers:
            for item in subscriber.calls:
                bindings += bind(item.handler._declared_call, container, FRAMEWORK)
                for depends in item.dependencies:
                    bindings += bind(depends.dependency, container, FRAMEWORK)
    return unique(bindings)
```

フレームワーク固有のことは 3 つあり、どのインテグレーションもそれをそのフレームワークの内部から見つけ出します。

- **シグネチャが読まれるタイミング。** `bind()` は、フレームワークがハンドラーのシグネチャを読むより前に実行しなければなりません。FastAPI はルートの宣言時に読むので、`nuke_di.fastapi` はルートクラスの中でバインドします。FastStream はサブスクライバーを作るとき、つまり起動のたびに読むので、上のモジュールは `call_decorators` フックでバインドします。
- **ハンドラーのある場所。** 起動時に `wrap_lifespan()` は `bindings()` を呼び出し、解決すべきクライアントを知ります。インテグレーションはアプリのルート、サブスクライバー、タスクをたどり、それぞれをバインドして `Binding` を返します。アプリのどこからも到達しないクライアントは接続されません。
- **lifespan のある場所。** `wrap_lifespan()` はアプリの lifespan を置き換えます。アプリ自身の lifespan はその内側で実行されるので、その起動処理でクライアントを使えます。

実際の `nuke_di.faststream` は、この例で省いたものを追加しています。FastStream 0.6、ブローカーとルーターの依存関係、そして複数のアプリがブローカーを共有するときのブローカーごとに 1 つの書き換えフックです。

### <a id="per-container"></a>`per_container`

`bind()` は関数をその場で書き換え、バインドしたコンテナを記憶します。`per_container=True`（デフォルト、FastAPI）では、あるコンテナにバインドされた関数が別のコンテナ向けに再び宣言されると、もう一度バインドされます。FastAPI はルートのシグネチャをルートの宣言時に一度だけ読むので、各アプリは取得したバインディングを保持し続け、2 つのコンテナ上の 2 つのアプリが同じ関数を同時に提供できます。

`per_container=False`（FastStream）では、関数はコンテナに関係なく一度だけバインドされ、起動するどのアプリも同じ `Binding` を解決します。FastStream は起動のたびにサブスクライバーを作り、テストブローカーの下ではアプリの lifespan が動く前にも作るので、シグネチャがアプリごとに変わってはいけません。その代償として、ハンドラー関数を共有する 2 つのアプリは 1 つずつ順番に実行することになり、2 つ目は `RuntimeError: ... is filled for another app that is running` を送出します。最初のアプリが起動した後でフレームワークがシグネチャを読み直す可能性があるなら、`False` を選んでください。

## <a id="a-framework-without-depends"></a>`Depends` のないフレームワーク

Litestar は依存関係を名前で提供し、aiogram はミドルウェアから名前で渡します。この場合 `bind()` は使えません。メッセージには `Framework`、ハンドラーのクライアント引数を見つけるには `client_of()`、クライアントごとに 1 つの `Binding`（その `get` はフレームワークが独自の方法で呼び出します）、そしてアプリの lifespan の中で `running()` を使います。実例は `nuke_di.litestar` です。引数の名前で `Provide(binding.get)` を登録しています。

## <a id="checking-an-integration"></a>インテグレーションを検証する

`nuke_di.integration.testing.check()` は、どのインテグレーションも守る契約を、あなたのインテグレーションに対して実行します。

- ハンドラーは型ヒントでクライアントを受け取り、そのクライアントはアプリが動いている間接続されている。
- ハンドラーの依存関係は型ヒントでクライアントを受け取る（`DependsFramework` の場合のみ）。
- 起動前の `override()` は、ハンドラーが別のクライアントを通じて受け取るクライアントを差し替える。
- アプリの lifespan なしで呼び出されたハンドラーは、フレームワークの `not_connected` エラーを送出する。
- `connect()` が失敗すると、アプリの起動は `RuntimeError` で失敗し、コンテナはフラッシュされた状態になる。

クライアントとハンドラーは `check()` が自前で用意します。あなたが渡すのはフレームワークと 2 つの関数です。`make_app(container, handler)` は、`container` でセットアップされ `handler` を提供する新しいアプリを返します。`handler` はクライアント以外の引数を持たない `async def` です。`run(app, lifespan)` はアプリを実行する非同期コンテキストマネージャーで、`lifespan` が true のときだけアプリの lifespan も実行し、`send()` を yield します。`send()` はフレームワークを通じてハンドラーを一度呼び出し、ハンドラーが送出したものを送出します。上のモジュールの場合は次のとおりです。

```python
# tests/test_contract.py
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from faststream import FastStream, TestApp
from faststream.nats import NatsBroker, TestNatsBroker

from myapp.faststream_di import FRAMEWORK, setup
from nuke_di import Dependencies
from nuke_di.integration.testing import Send, check


def make_app(container: Dependencies, handler: Callable[..., Any]) -> FastStream:
    broker = NatsBroker()
    app = FastStream(broker)
    setup(app, container)
    broker.subscriber("check")(handler)
    return app


@asynccontextmanager
async def run(app: FastStream, lifespan: bool) -> AsyncIterator[Send]:
    # connect_only: FastStream would guess it from the mention of TestApp below
    async with TestNatsBroker(app.broker, connect_only=lifespan) as broker:
        if lifespan:
            async with TestApp(app):
                yield lambda: broker.publish(None, "check")
        else:
            yield lambda: broker.publish(None, "check")


async def test_contract() -> None:
    await check(FRAMEWORK, make_app, run)
```

```console
$ pytest -q tests/test_contract.py
.                                                                        [100%]
1 passed in 0.29s
```

`check()` はコルーチンなので、pytest-asyncio か anyio の下で実行してください。失敗したすべてのケースを `ExceptionGroup` として送出し、それぞれにケース名を示すノートが付きます。`setup()` から `wrap_lifespan()` の行を除くと、コンテナは一度も接続されません。

```console
$ pytest -q --tb=short tests/test_contract.py
F                                                                        [100%]
  | ExceptionGroup: the FastStream integration breaks the nuke-di contract (4 sub-exceptions)
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case handler: a handler takes a client by type hint, connected for the time the app runs
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case dependency: a dependency of a handler takes a client by type hint
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case override: override() before startup replaces a client the handler gets through another client
    | AssertionError: expected a RuntimeError with 'nuke-di clients failed to start' about Broken, got RuntimeError('Broken is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`')
    | FastStream integration, case failed_connect: a failed connect() fails the app's startup with a RuntimeError and leaves the container flushed
FAILED tests/test_contract.py::test_contract - ExceptionGroup: the FastStream...
1 failed in 0.17s
```

（トレースバックは省略しています。）ハンドラーのエラーを送出せずに報告するフレームワークでは、それを送出する `send()` が必要です。HTTP フレームワークのテストクライアントは 500 を返すので、`send()` がステータスを確認します。Litestar がエラーをレスポンスに含めるのは `debug=True` のときだけです。

1.14.0 より前にこのキットが置かれていたプライベートモジュール `nuke_di._integration` は、`DeprecationWarning` 付きで引き続きインポートできますが、1.15.0 で削除されます。
