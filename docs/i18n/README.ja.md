# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](ja/development.md)
[![License](https://img.shields.io/pypi/l/nuke-di)](../../LICENSE)

[English](https://github.com/troyan-dy/nuke-di/blob/master/README.md) · [Русский](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ru.md) · [简体中文](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.zh-CN.md) · [Español](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.es.md) · [Português (Brasil)](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pt-BR.md) · **日本語** · [Polski](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pl.md)

非同期 Python プロジェクトのための、いちばんシンプルな依存性注入（DI）です。

依存関係は普通の型ヒントで宣言します。`nuke-di` は依存関係ツリーを構築し、各クライアントを一度だけ生成して、その非同期ライフサイクルを管理します。起動時には `connect()` を、終了時には `disconnect()` を呼び出します。各クライアントは自身の依存先の接続が終わるとすぐに起動し、準備ができた他のすべてのクライアントと並行して接続されます。

さらに、デコレーターをひとつ付けるだけで async 関数がコマンドライン引数を持つプロセスになり、FastAPI、Litestar、FastStream のハンドラーも、同じように型ヒントでクライアントを受け取ります。

本番運用されている Python マイクロサービスフレームワークの DI 機構を切り出したもので、実行時の依存パッケージはありません。

- [インストール](#installation) · [クイックスタート](#quick-start) · [原則](#principles) · [パフォーマンス](#performance)
- 例：[コマンドライン引数を持つジョブ](#a-job-with-command-line-arguments) · [FastAPI](#fastapi)
- [ドキュメント](#documentation)

## <a id="installation"></a>インストール

```bash
pip install nuke-di
```

Python 3.11 以上が必要です。

## <a id="quick-start"></a>クイックスタート

```python
import asyncio

from nuke_di import DI, Client


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


async def handler(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def main() -> None:
    injected = DI.inject(handler)  # resolves UserService -> Database

    async with DI:  # connect() every client, disconnect() on exit
        print(await injected(42))


asyncio.run(main())
```

```text
database: connected
Hello, user-42!
database: disconnected
```

何が起きたのか：

1. `DI.inject(handler)` は `handler` の型ヒントを読み取ってクライアント `UserService` を見つけ、その `__init__` が `Database` を必要としていることを確認して、両方を構築しました。`user_id: int` はクライアントではないので、通常の引数のまま残ります。
2. `async with DI` は、構築したすべてのクライアントに対して、依存先から順に `connect()` を呼び出しました。
3. `injected(42)` は `handler(42, users=<UserService>)` を呼び出しました。
4. `async with` ブロックを抜けると、逆の順序で `disconnect()` が呼び出されました。

## <a id="principles"></a>原則

- **依存関係はクラスである。** 型注釈付きの `__init__` と非同期の `connect()` / `disconnect()` を持つ `Client` のサブクラス、それがモデルのすべてです。プロバイダーも、モジュールも、登録も、設定すべきスコープもありません。サードパーティのオブジェクトは、そうしたクラスでラップすることで依存関係になります。
- **型ヒントが配線である。** クライアントは自身の依存関係を `__init__` で、関数はシグネチャで要求します。それ以外にそれらを名指しする場所はないので、依存関係の名前の変更や追加は普通のリファクタリングです。
- **並行して起動し、順序どおりに停止する。** クライアントは自身の依存先の接続が終わるとすぐに、準備ができた他のすべてのクライアントと並行して接続するので、遅いクライアントが待たせるのはそれを必要とするクライアントだけです。切断は逆順に行われ、ある `disconnect()` が失敗しても他のクライアントの切断は止まりません。
- **フェイルファスト。** 構築できないツリーは、何かが接続される前に、該当する引数とそこまでの経路を示して失敗します。[プラグイン](ja/clients.md#checking-the-tree-with-mypy)を有効にした `mypy` も、プロセスが起動する前に同じエラーを報告します。接続できないクライアントがあると、接続済みのクライアントを切断したうえでアプリケーションが停止します。リトライはありません。再起動はオーケストレーターの役割です。
- **テストは差し替えるだけで、配線し直さない。** `mock()` と `override()` は、1 つのテストの間だけクライアントの代わりにフェイクを置きます。テスト対象のコードは変わりません。
- **実行時の依存パッケージはない。** コアは標準ライブラリだけを使い、フレームワークとの統合はエクストラとして提供されます。

起動の流れを[クライアントのガイド](ja/clients.md#connect-order)の例で示します。`Consumer` が必要とするのは `Kafka` だけなので、遅い `Postgres` を待たず、起動にかかる時間は最も長い依存関係の連鎖の時間になります。

![6 つのクライアントが自身の依存先に従って接続する様子：Kafka と Redis の接続が終わるとすぐに Consumer と Http が開始し、起動は 0.35s](https://raw.githubusercontent.com/troyan-dy/nuke-di/master/docs/connect-now.svg)

## <a id="performance"></a>パフォーマンス

`benchmarks/compare.py` は同じクライアントのツリーを dishka、wireup、dependency-injector、injector に通します。各ライブラリは同じクラス群を
自分の流儀で登録し、プロセスにとって新しいクラスでルートを解決したコールドなコンテナ、ルートの再取得、各ライブラリの統合経由の
FastAPI リクエスト 1 回を計ります。

```console
$ uv run python benchmarks/compare.py --size 100 --summary
nuke-di 1.11.1 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 6c2ae10 · N = 100 · 20 repeats
nuke-di 1.11.1 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

| Lower is better                                          | nuke-di        | dishka          | wireup          | dependency-injector | injector        |
|----------------------------------------------------------|---------------:|----------------:|----------------:|--------------------:|----------------:|
| Cold start: a container and a tree of 100 clients        | **541 µs**     | 12.9 ms (23.8×) | 20.0 ms (37.0×) | 1.05 ms (1.9×)      | 1.34 ms (2.5×)  |
| Cold start: the same 100 clients with string annotations | 1.27 ms (1.2×) | 13.7 ms (12.6×) | 21.4 ms (19.6×) | **1.09 ms**         | 1.47 ms (1.3×)  |
| A cached root                                            | 94.1 ns (2.5×) | 261 ns (7.1×)   | 92.6 ns (2.5×)  | **37.0 ns**         | 1.18 µs (31.9×) |
| A FastAPI request with a client                          | **103 µs**     | 107 µs (1.0×)   | 206 µs (2.0×)   | 221 µs (2.2×)       | —               |
```

![nuke-di against other DI libraries: lower is better](../benchmarks/compare.png)

では `nuke-di` は最速なのでしょうか。実際の型ヒントでのツリーの構築と FastAPI リクエストでは、はい。dependency-injector と
injector はツリーの構築に 2–2.5 倍、dishka と wireup はコンテナ作成時のグラフ検証のために 24–37 倍の時間がかかり、wireup と
dependency-injector はリクエストごとに 2 倍を払います。文字列のアノテーションでは、アノテーションを一切読まない
dependency-injector が 2 割ほど先行します。キャッシュ済みのルートでは `nuke-di` は wireup と互角で、dependency-injector の Cython 製
`get()` が約 50 ns 速く、これはどのアプリケーションも気づかない差です。

単体では、`resolve()` はクライアント 1 件あたり 3.5–6.3 µs なので、1000 クライアントのツリーは 5.5 ms 未満で構築されます。`connect()` は
クライアント 1 件あたり 13–18 µs を加えます。[docs/benchmarks.md](../benchmarks.md) は各シナリオを説明し、Python 3.11–3.14 の
ベースラインを記録し、比較の全体をその方法と共に載せています。

## <a id="a-job-with-command-line-arguments"></a>コマンドライン引数を持つジョブ

デコレーターをひとつ付けるだけで、async 関数がプロセスのメインプログラムになります。クライアントは注入され、それ以外の型注釈付きの引数はすべて、型付きで検証されるコマンドラインオプションになります。

```python
# sync.py
import datetime
import enum
from typing import Annotated

from nuke_di import Client, Option, job


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")

    async def upsert(self, table: str, rows: list[str]) -> None:
        print(f"postgres: upserted {len(rows)} rows into {table}")


class Warehouse(Client):
    async def changes(self, table: str, day: datetime.date) -> list[str]:
        return [f"{table}:{day}:{n}" for n in range(3)]


class Mode(enum.Enum):
    INCREMENTAL = "incremental"
    FULL = "full"


@job
async def sync(
    pg: Postgres,
    warehouse: Warehouse,
    day: Annotated[datetime.date, Option(help="Day to copy, YYYY-MM-DD", short="d")],
    tables: Annotated[
        list[str] | None, Option(help="Table to copy, repeat for several; all by default", short="t")
    ] = None,
    mode: Mode = Mode.INCREMENTAL,
    dry_run: Annotated[bool, Option(help="Read the changes, write nothing")] = False,
) -> None:
    """Copy one day of changes from the warehouse into Postgres."""
    print(f"sync: {mode.name} copy of {day}")
    for table in tables or ["users", "orders"]:
        rows = await warehouse.changes(table, day)
        if dry_run:
            print(f"sync: would upsert {len(rows)} rows into {table}")
        else:
            await pg.upsert(table, rows)
```

`main()` も `asyncio.run()` も `argparse` も要りません。デコレーターがクライアントを解決して接続し、コマンドラインを解析し、関数を実行して、意味のある終了コードで終了します。

```console
$ python sync.py --day 2026-10-01
postgres: connected
sync: INCREMENTAL copy of 2026-10-01
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected

$ python sync.py -d 2026-10-01 -t users --mode FULL --dry-run
postgres: connected
sync: FULL copy of 2026-10-01
sync: would upsert 3 rows into users
postgres: disconnected
```

`--help` はシグネチャと docstring から生成されます（Python 3.13 以降では `-d DAY, --day DAY` ではなく `-d, --day DAY` と表示されます）。

```console
$ python sync.py --help
usage: sync.py [-h] -d DAY [-t TABLES] [--mode {INCREMENTAL,FULL}]
               [--dry-run | --no-dry-run]

Copy one day of changes from the warehouse into Postgres.

options:
  -h, --help            show this help message and exit
  -d DAY, --day DAY     Day to copy, YYYY-MM-DD
  -t TABLES, --tables TABLES
                        Table to copy, repeat for several; all by default
  --mode {INCREMENTAL,FULL}
                        (default: INCREMENTAL)
  --dry-run, --no-dry-run
                        Read the changes, write nothing (default: False)
```

誤ったコマンドラインは、クライアントが接続されるより前に、終了コード `2` で拒否されます。

```console
$ python sync.py -d 2026-10-01 --mode full
usage: sync.py [-h] -d DAY [-t TABLES] [--mode {INCREMENTAL,FULL}]
               [--dry-run | --no-dry-run]
sync.py: error: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
Run sync.sync failed: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
$ echo $?
2
```

`@worker` は、SIGTERM を受け取るまで動き続けるプロセスに対して同じことを、グレースフルシャットダウン付きで行います。どちらも[ワーカーとジョブ](ja/workers-and-jobs.md)で説明しています。

## <a id="fastapi"></a>FastAPI

パスオペレーションは型ヒントでクライアントを受け取ります。ハンドラーごとの `Depends` も `inject()` も不要です。`app/clients.py` には[クイックスタート](#quick-start)の `Database` クラスと `UserService` クラスを、`main()` を除いて置いています。

```bash
pip install "nuke-di[fastapi]"
```

```python
# app/api.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di.fastapi import setup

app = FastAPI()
setup(app)  # before the routes: clients connect on startup, disconnect on shutdown


@app.get("/users/{user_id}")
async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


@app.get("/me")
async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user
```

```console
$ uvicorn app.api:app
INFO:     Started server process [55625]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51602 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51604 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [55625]
```

```console
$ curl localhost:8000/users/42
"Hello, user-42!"
$ curl localhost:8000/me -H "X-User-Id: 7"
"user-7"
```

クライアントは起動時に接続し、終了時に切断します。`current_user` のような依存関係も、同じ方法でクライアントを受け取ります。アプリをインポートしても何も構築されないので、テストでは `TestClient` がアプリを起動する前にクライアントを差し替えます。

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
1 passed in 0.23s
```

ルーター、WebSocket、アプリ自身の lifespan については [FastAPI](ja/fastapi.md) で説明しています。[Litestar](ja/litestar.md) と [FastStream](ja/faststream.md) も同じように動作します。

## <a id="documentation"></a>ドキュメント

- [クライアント](ja/clients.md)：`Client` と `NotSingletonClient`、ライフサイクル、データクラスのクライアント、接続順序、起動時間、依存グラフ、接続エラーと解決エラー
- [コンテナ](ja/container.md)：`Dependencies` とグローバルな `DI`、`resolve()`、`inject()`、`mock()`、`override()`
- [ワーカーとジョブ](ja/workers-and-jobs.md)：`@job` と `@worker`、コマンドラインパラメータ、`Shutdown`、猶予期間、バックグラウンドタスク、終了コード、フック、Kubernetes
- フレームワーク：[FastAPI](ja/fastapi.md)、[Litestar](ja/litestar.md)、[FastStream](ja/faststream.md)
- [テスト](ja/testing.md)：`mock()`、`override()`、pytest フィクスチャ、配線の確認
- [設定](ja/configuration.md)：タイムアウト、並行数、猶予期間
- [エラー](ja/errors.md)：すべての例外と、それが送出される条件
- [サンプル](../../examples/README.md): すぐに動かせる 21 のシナリオ。単発のスクリプトやキューの worker から
  FastAPI、Litestar、FastStream、Starlette、サービス全体まで、それぞれ出力とテスト付き
- [ベンチマーク](../benchmarks.md)：すべてのシナリオ、Python 3.11–3.14 のベースライン、他のライブラリとの比較
- [開発](ja/development.md)：チェック、カバレッジ、リリース

## <a id="license"></a>ライセンス

[MIT](../../LICENSE)
