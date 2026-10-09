# <a id="testing"></a>テスト

[English](../../guide/testing.md) · [Русский](../ru/testing.md) · [简体中文](../zh-CN/testing.md) · [Español](../es/testing.md) · [Português (Brasil)](../pt-BR/testing.md) · **日本語** · [Polski](../pl/testing.md)

← [ドキュメント](../README.ja.md#documentation)

**コンテナ経由でクライアントをテストする。** ツリーを解決する前にモックを登録しておけば、すべての利用側がそのモックを受け取ります。

```python
from unittest.mock import call

from nuke_di import Dependencies


async def test_greet() -> None:
    deps = Dependencies()
    db = deps.mock(Database)
    db.fetch_user.return_value = "alice"

    users = deps.resolve(UserService)
    async with deps:
        assert await users.greet(1) == "Hello, alice!"

    assert db.fetch_user.await_args_list == [call(1)]
```

**`override()` でブロックの間だけクライアントを差し替える。** `override(cls, new=None)` は `mock()` と同じように差し替えを登録しますが、その有効期間は `with` ブロックの終わりまでで、ブロック内で `async with` のサイクルを何度繰り返しても維持されます。ブロックを抜けるとコンテナがフラッシュされるので、ブロック内で解決したものが次のテストに漏れ出すことはありません。グローバルな `DI` でも使えます。

```python
# test_greet.py, with Database, UserService and handler from the Quick start
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_greet_with_fake() -> None:
    with DI.override(Database, FakeDatabase()):
        injected = DI.inject(handler)
        async with DI:
            print(await injected(1))

    print("after the block:", DI.clients)


async def test_greet_with_autospec() -> None:
    with DI.override(Database) as db:  # an autospec mock by default
        db.fetch_user.return_value = "bob"
        injected = DI.inject(handler)
        async with DI:
            print(await injected(2))

    db.fetch_user.assert_awaited_once_with(2)
```

このセクションの非同期テストは、`pytest.ini` に `asyncio_mode = auto` を設定した [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) を使っています。これがないと、pytest は `async def` のテストを実行しません。

```console
$ pytest -q -s test_greet.py
Hello, alice!
after the block: OrderedDict()
.Hello, bob!
.
2 passed in 0.01s
```

`database: connected` は一度も出力されません。差し替えは接続されないからです。

ルール：

- **解決する前に差し替える。** `cls` が解決された後に差し替えを登録しても、それ以降に解決された利用側にしか届かず、先に解決された利用側は本物のクライアントを持ったままになります。そのため、この場合 `mock()` は例外を送出します。

  ```python
  DI.inject(handler)  # resolves UserService -> Database
  DI.mock(Database)  # ConnectError: Database is already resolved, call mock() before resolve() or inject()
  ```

- **`override()` は解決済みクライアントのないコンテナから始める。** そうでないと、終了時のフラッシュによって、ブロックより前に解決されたものが黙って破棄されてしまうため、`ConnectError: override(Database) needs a container without resolved clients, found: Database, UserService` が送出されます。先に `DI.flush()` を呼び出すか、後述の `global_di` フィクスチャを使ってください。
- **差し替えはクラスごとにひとつ。** `mock(cls)` をもう一度呼び出すと、すでに登録されている差し替えが返されます。`mock(cls, other)` と `override(cls)` は `ConnectError: Database already has a replacement` を送出します。
- **差し替えは接続されない。** 差し替えの `connect()` / `disconnect()` が呼び出されることはなく、[レイヤー](clients.md#layers)にも加わりません。
- **差し替えの有効期間。** `mock()` で登録した差し替えは、`disconnect()` の最後に行われるものも含め、次の `flush()` で破棄されます。コンテナを複数回接続するテストでは `override()` を使ってください。その差し替えは、ブロックが終わるまでどの `flush()` を経ても残ります。ブロック内で発生した例外はそのまま伝播します。コンテナが接続されたままブロックを正常に抜けると、`ConnectError` が送出されます。
- **ネスト。** 異なるクラスのブロックは、それぞれが何かを解決する前に開かれている限りネストできます（例：`with DI.override(Database), DI.override(Clock):`）。内側のブロックを抜けても、外側の差し替えは残ります。

**pytest フィクスチャ。** `nuke-di` をインストールすると、2 つのフィクスチャを提供する pytest プラグインが登録されます。どちらも autouse ではないので、既存のテストはこれまでとまったく同じように動作します。

| フィクスチャ | 提供するもの                                           |
|--------------|--------------------------------------------------------|
| `di`         | テストごとに新しい `Dependencies`                      |
| `global_di`  | テストの前後でフラッシュされるグローバルな `DI`        |

コンテナを接続したまま終了したテストは teardown でエラーになりますが、それでもコンテナはフラッシュされるので、次のテストはクリーンな状態で始まります。

```python
# test_users.py, with Database, UserService and handler from the Quick start
from nuke_di import Dependencies


async def test_greet(di: Dependencies) -> None:
    di.mock(Database).fetch_user.return_value = "alice"
    users = di.resolve(UserService)
    async with di:
        assert await users.greet(1) == "Hello, alice!"


async def test_handler(global_di: Dependencies) -> None:  # e.g. code that calls DI.inject()
    global_di.mock(Database).fetch_user.return_value = "bob"
    injected = global_di.inject(handler)
    async with global_di:
        assert await injected(2) == "Hello, bob!"


async def test_forgets_to_disconnect(di: Dependencies) -> None:
    di.resolve(UserService)
    await di.connect()
```

```console
$ pytest -q test_users.py
...E                                                                     [100%]
==================================== ERRORS ====================================
_______________ ERROR at teardown of test_forgets_to_disconnect ________________
the test left the container of the "di" fixture connected; its clients were not disconnected, use `async with` or call disconnect()
----------------------------- Captured stdout call -----------------------------
database: connected
=========================== short test summary info ============================
ERROR test_users.py::test_forgets_to_disconnect - Failed: the test left the c...
3 passed, 1 error in 0.01s
```

フィクスチャが、切断し忘れたコンテナを代わりに切断することはできません。teardown の時点では、テストのイベントループがすでに閉じている可能性があるからです。`global_di` が保護するのは、それを要求したテストだけです。`global_di` を使わずにグローバルな `DI` を使うテストは、次のテストにクライアントを残してしまう可能性があります。独自の `di` フィクスチャを定義しているプロジェクトでは、それがそのまま使われます。`conftest.py` のフィクスチャはプラグインのフィクスチャより優先されるからです。プラグインは `pytest -p no:nuke_di` で無効にできます。

**ジョブを直接テストする。** モジュールをインポートしてもジョブは実行されないので、モックとパラメータを渡して関数を呼び出します。

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    pg.upsert.assert_awaited_once_with("users", ["row"])
```

**コンテナ経由でジョブをテストする**と、クライアントが本番と同じように組み立てられます。

```python
async def test_sync_with_container() -> None:
    deps = Dependencies()
    pg = deps.mock(Postgres)  # mocks first: resolve() and inject() reuse them
    warehouse = deps.mock(Warehouse)
    warehouse.changes.return_value = ["row"]
    injected = deps.inject(sync)

    async with deps:
        await injected(day=datetime.date(2026, 10, 1), tables=["users"])

    assert pg.upsert.await_args_list == [call("users", ["row"])]
```

**すべてのエントリーポイントが解決できる。** モジュールをインポートしてもジョブやワーカーは実行されず、`inject()` は何も接続
せずにツリーを組み立てます。そのため、1 つのテストで CI 上ですべてのエントリーポイントの配線を確認できます。循環、型ヒントの
ない引数、クライアントではない必須引数、例外を投げる `__init__` は、実際の実行が出すのと同じエラーでテストを失敗させ、
データベースは不要です。

```python
# test_wiring.py
from collections.abc import Callable

import pytest

from nuke_di import Dependencies

from app.jobs import sync
from app.workers import consumer


@pytest.mark.parametrize("entrypoint", [sync.sync, consumer.consumer])
def test_entrypoint_resolves(entrypoint: Callable[..., object]) -> None:
    Dependencies().inject(entrypoint)  # runs every __init__, connects nothing
```

```console
$ pytest -q test_wiring.py
..                                                                       [100%]
2 passed in 0.05s
```

[mypy プラグイン](clients.md#checking-the-tree-with-mypy)は、何も実行せずに同じシグネチャのエラーと循環を報告します。このテストは
すべての `__init__` も実行するので、例外を投げる `__init__` も検出します。

コンテナを保持すれば、そのエントリーポイントの[依存グラフ](clients.md#the-graph)が README 用に得られます：
`deps = Dependencies(); deps.inject(sync.sync); print(deps.graph().to_mermaid())`。

**ワーカーをテストする。** `Shutdown.set()` は SIGTERM と同じ働きをします。

```python
async def test_consumer_stops_on_shutdown() -> None:
    queue, shutdown = AsyncMock(), Shutdown()

    async def last_message() -> str:
        shutdown.set()  # what SIGTERM would do
        return "message-1"

    queue.get.side_effect = last_message

    await consumer(queue, shutdown)

    queue.get.assert_awaited_once()
```
