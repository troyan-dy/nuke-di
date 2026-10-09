# <a id="workers-and-jobs"></a>ワーカーとジョブ

[English](../../guide/workers-and-jobs.md) · [Русский](../ru/workers-and-jobs.md) · [简体中文](../zh-CN/workers-and-jobs.md) · [Español](../es/workers-and-jobs.md) · [Português (Brasil)](../pt-BR/workers-and-jobs.md) · **日本語** · [Polski](../pl/workers-and-jobs.md)

← [ドキュメント](../README.ja.md#documentation)

デコレーターをひとつ付けるだけで、async 関数がプロセスのメインプログラムになります。

| デコレーター | 実行のしかた                                           |
|--------------|--------------------------------------------------------|
| `@job`       | 一度だけ：関数が戻るとプロセスが終了します             |
| `@worker`    | プロセスが SIGTERM または SIGINT を受け取るまで        |

このセクションの例では、次のクライアントモジュールを共通で使います。

```python
# app/clients.py
import datetime
import itertools

from nuke_di import Client


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")

    async def upsert(self, table: str, rows: list[str]) -> None:
        print(f"postgres: upserted {len(rows)} rows into {table}")


class Warehouse(Client):
    async def connect(self) -> None:
        print("warehouse: connected")

    async def disconnect(self) -> None:
        print("warehouse: disconnected")

    async def changes(self, table: str, day: datetime.date) -> list[str]:
        return [f"{table}:{day}:{n}" for n in range(3)]


class Queue(Client):
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    async def connect(self) -> None:
        print("queue: connected")

    async def disconnect(self) -> None:
        print("queue: disconnected")

    async def get(self) -> str:
        return f"message-{next(self._ids)}"
```

## <a id="your-first-job"></a>はじめてのジョブ

```python
# app/jobs/sync.py
import datetime

from nuke_di import job

from app.clients import Postgres, Warehouse


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None:
    day = datetime.date.today() - datetime.timedelta(days=1)
    for table in ["users", "orders"]:
        await pg.upsert(table, await warehouse.changes(table, day))
```

```console
$ python -m app.jobs.sync
postgres: connected
warehouse: connected
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
$ echo $?
0
```

プログラムはこれだけです。`main()` も `asyncio.run()` も `if __name__ == "__main__"` も要りません。プロセスはグローバルな `DI` コンテナからクライアントを解決して接続し、関数を実行し、クライアントを切断してから、[終了コード](#exit-codes)を返して終了します。スケジューリングはライブラリの役割ではありません。ジョブをいつ実行するかは、Kubernetes の CronJob、systemd タイマー、crontab などが決めます。

`nuke-di` はすべての実行を `nuke_di` ロガーに記録します。これを表示するには、デコレーターより上でロギングを設定します。

```python
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s: %(message)s")


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None: ...
```

```console
$ python -m app.jobs.sync
INFO  nuke_di.run: Starting job app.jobs.sync.sync
postgres: connected
warehouse: connected
INFO  nuke_di.core: Connected 4 clients in 1 layer in 0.00s (slowest: Warehouse 0.00s, Postgres 0.00s, Shutdown 0.00s)
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
INFO  nuke_di.run: Run app.jobs.sync.sync finished with exit code 0 in 0.002s
```

### <a id="one-entrypoint-per-module-defined-last"></a>エントリーポイントはモジュールにひとつ、最後に定義する

モジュールが `__main__` として実行されると、デコレーターはその場で関数を実行し、プロセスはそこで終了します。

```python
# app/jobs/sync.py
DI.mock(Warehouse, FakeWarehouse())  # runs: code above the decorator is fine


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None: ...


print("never printed")  # never runs under `python -m app.jobs.sync`
```

**エントリーポイントはモジュールにひとつだけにして、最後に定義してください。** テストからインポートするなど通常のインポートでは、デコレーターは関数をそのまま返し、何も実行されません。デコレートする関数は `async def` で宣言する必要があり、そうでない場合はインポート時に `TypeError` が送出されます。

## <a id="parameters"></a>パラメータ

型注釈付きの引数のうちクライアントでないものは、すべてコマンドラインオプションになります。次は先ほどと同じジョブですが、任意の日付のデータを、一部のテーブルだけ、ドライランでコピーできるようになっています。

```python
# app/jobs/sync.py
import datetime
import enum
from typing import Annotated

from nuke_di import Option, job

from app.clients import Postgres, Warehouse


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

`pg` と `warehouse` はクライアントなので注入されます。`day`、`tables`、`mode`、`dry_run` はコマンドラインから渡されます。

```console
$ python -m app.jobs.sync --day 2026-10-01
postgres: connected
warehouse: connected
sync: INCREMENTAL copy of 2026-10-01
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected

$ python -m app.jobs.sync -d 2026-10-01 -t users --mode FULL --dry-run
postgres: connected
warehouse: connected
sync: FULL copy of 2026-10-01
sync: would upsert 3 rows into users
postgres: disconnected
warehouse: disconnected
```

`--help` はシグネチャと docstring から生成されます。このとき何も接続されません（Python 3.13 以降では `-d DAY, --day DAY` ではなく `-d, --day DAY` と表示されます）。

```console
$ python -m app.jobs.sync --help
usage: python -m app.jobs.sync [-h] -d DAY [-t TABLES]
                               [--mode {INCREMENTAL,FULL}]
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

誤ったコマンドラインは、**クライアントが解決・接続されるより前に**、終了コード `2` で拒否されます。

```console
$ python -m app.jobs.sync
usage: python -m app.jobs.sync [-h] -d DAY [-t TABLES]
                               [--mode {INCREMENTAL,FULL}]
                               [--dry-run | --no-dry-run]
python -m app.jobs.sync: error: the following arguments are required: -d/--day
Run app.jobs.sync.sync failed: the following arguments are required: -d/--day
$ echo $?
2

$ python -m app.jobs.sync --day yesterday
...
python -m app.jobs.sync: error: argument -d/--day: invalid date value: 'yesterday'

$ python -m app.jobs.sync -d 2026-10-01 --mode full
...
python -m app.jobs.sync: error: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)

$ python -m app.jobs.sync -d 2026-10-01 --dry
...
python -m app.jobs.sync: error: unrecognized arguments: --dry
```

各エラーの最初の 2 行は `argparse` が出力したものです。`Run ... failed` の行は `nuke_di` ロガーの `ERROR` レコードなので、ロギングの設定に従います。省略形は受け付けません。`--dry` が `--dry-run` とみなされることはありません。

### <a id="supported-types"></a>サポートされる型

| アノテーション                                | コマンドライン                  | 例                              |
|-----------------------------------------------|---------------------------------|---------------------------------|
| `str`, `int`, `float`, `pathlib.Path`         | `--name VALUE`                  | `--limit 10`                    |
| `bool`                                        | `--name` / `--no-name`          | `--dry-run`                     |
| `datetime.date`, `datetime.datetime`          | ISO 8601                        | `--since 2026-10-01T12:00:00`   |
| `Enum`                                        | メンバーの**名前**（書かれたとおり） | `--mode FULL`              |
| `bool` 以外の上記いずれかの型の `list[T]`     | オプションを繰り返す            | `--table users --table orders`  |
| `T \| None`                                   | `T` と同じ                      | `--limit 10`                    |

ルール：

- **名前。** オプション名は、引数名の `_` を `-` に置き換えたものです。`dry_run` は `--dry-run` になります。位置引数はないので、パラメータを追加しても既存のコマンドラインが壊れることはありません。
- **必須かどうか。** デフォルト値のない引数は必須オプションになります。デフォルト値のある引数は省略可能で、オプションが省略された場合は関数自身のデフォルト値が使われます。
- **`Option`。** `Annotated[T, Option(help=..., short=...)]` で、ヘルプテキストと `-d` のような 1 文字のエイリアスを追加できます。どちらも省略可能です。
- **パラメータがない場合。** パラメータを持たないエントリーポイントもコマンドラインを解析します。`--help` には応答し、それ以外の引数はすべて終了コード `2` で拒否します。

次のようなシグネチャは、コマンドラインではなくコードのバグです。`InvalidSignatureError` と終了コード `1` で実行が失敗します。

```python
async def sync(day: dict[str, int]) -> None: ...  # unsupported type
async def sync(pg: Annotated[Postgres, Option(help="...")]) -> None: ...  # Option on a client
async def sync(help: bool = False) -> None: ...  # clashes with --help
async def sync(day: int, /) -> None: ...  # positional-only
```

### <a id="parameters-in-tests"></a>テストでのパラメータ

デコレートされた関数は普通のコルーチンのままなので、テストではパラメータをキーワード引数として渡します。

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    warehouse.changes.assert_awaited_once_with("users", datetime.date(2026, 10, 1))
    pg.upsert.assert_awaited_once_with("users", ["row"])
```

## <a id="your-first-worker"></a>はじめてのワーカー

ワーカーは、プロセスに停止が要求されるまで動き続けます。ワーカーは最初の SIGTERM または SIGINT でセットされる `Shutdown` クライアントに依存し、処理中の作業を終えてから停止します。

```python
# app/workers/consumer.py
import asyncio

from nuke_di import Shutdown, worker

from app.clients import Queue


@worker
async def consumer(queue: Queue, shutdown: Shutdown) -> None:
    while not shutdown.is_set():
        message = await queue.get()
        print(f"consumer: processing {message}")
        await asyncio.sleep(1)  # the actual work
        print(f"consumer: done {message}")
    print("consumer: stopped")
```

3 つ目のメッセージの処理中に Ctrl+C を押すと、そのメッセージの処理を終えてからループを抜け、クライアントが切断されます。

```console
$ python -m app.workers.consumer
queue: connected
consumer: processing message-1
consumer: done message-1
consumer: processing message-2
consumer: done message-2
consumer: processing message-3
^C
consumer: done message-3
consumer: stopped
queue: disconnected
$ echo $?
130
```

`Shutdown` には 3 つのメソッドがあります。

| メソッド         | 説明                                                                      |
|------------------|---------------------------------------------------------------------------|
| `is_set()`       | シャットダウンが始まったかどうか。作業の区切りごとに確認します            |
| `await wait()`   | シャットダウンが始まるまでブロックします                                  |
| `set()`          | シャットダウンを手動で開始します（テストなどで使います）                  |

ワーカーやジョブの外ではこれをセットするものがないので、`Shutdown` に依存するループは Web アプリケーションの中でもそのまま動作します。ワーカーが自ら戻ったり例外を送出したりした場合も、プロセスは終了します。再起動はオーケストレーターの役割です。

Windows で処理されるのは SIGINT（Ctrl+C）だけで、SIGTERM はデフォルトの動作のままです。

## <a id="grace-period"></a>猶予期間

`Shutdown` を無視するワーカーは、`SHUTDOWN_GRACE_SECONDS`（デフォルト `10`）秒が経過するとキャンセルされます。

```python
# app/workers/stubborn.py
@worker
async def stubborn(queue: Queue) -> None:
    while True:  # never looks at Shutdown
        message = await queue.get()
        print(f"stubborn: processing {message}")
        await asyncio.sleep(5)
```

```console
$ SHUTDOWN_GRACE_SECONDS=2 python -m app.workers.stubborn &
queue: connected
stubborn: processing message-1
$ kill -TERM %1
Run app.workers.stubborn.stubborn did not stop within 2.0s after Shutdown, cancelling it
queue: disconnected
$ wait %1; echo $?
143
```

2 回目のシグナルを受け取ると、猶予期間を待たずにただちにエントリーポイントがキャンセルされます。たとえば Ctrl+C を 2 回押した場合です。

```console
$ python -m app.workers.stubborn
queue: connected
stubborn: processing message-1
^C^C
Second SIGINT, cancelling run app.workers.stubborn.stubborn
queue: disconnected
```

クライアントの接続中にシグナルが届いた場合は起動が中止され、すでに接続済みのクライアントは切断されます。

最悪の場合、プロセスが停止するまでに `SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers` かかります。デフォルト値のままだと、2 レイヤーのツリーだけで Kubernetes のデフォルトの `terminationGracePeriodSeconds` である 30 秒を使い切ってしまいます。ツリーがもっと深い場合は、タイムアウトを短くするか猶予期間を長くしてください。

## <a id="background-tasks"></a>バックグラウンドタスク

`BackgroundTasks` は、エントリーポイントと並行して動くコルーチンを監督するクライアントです。素の `asyncio.create_task()` とは異なり、失敗したタスクが握りつぶされることはありません。トレースバック付きでログに記録され、プロセス全体が失敗します。

```python
# app/workers/indexer.py
import asyncio

from nuke_di import BackgroundTasks, Shutdown, worker

from app.clients import Queue


async def refresh_index() -> None:
    for attempt in range(1, 10):
        print(f"refresh: run {attempt}")
        await asyncio.sleep(0.5)
        if attempt == 2:
            raise ConnectionError("search cluster is unreachable")


@worker
async def indexer(queue: Queue, tasks: BackgroundTasks, shutdown: Shutdown) -> None:
    tasks.spawn(refresh_index(), name="refresh-index")
    print("indexer: waiting for Shutdown")
    await shutdown.wait()
```

```console
$ python -m app.workers.indexer
queue: connected
indexer: waiting for Shutdown
refresh: run 1
refresh: run 2
Background task refresh-index failed
Traceback (most recent call last):
  ...
ConnectionError: search cluster is unreachable
queue: disconnected
Run app.workers.indexer.indexer failed
Traceback (most recent call last):
  ...
ConnectionError: search cluster is unreachable
$ echo $?
1
```

ワーカーは猶予期間なしでキャンセルされました。バックグラウンドのループがクラッシュしたのに、何もしないプロセスが生き残ってはならないからです。プロセスがどんな理由で停止する場合でも、タスクはクライアントが切断される**前に**キャンセルされ、その終了が待たれます。そのため、タスクが閉じたクライアントに対して動作することはありません。

| メソッド                   | 説明                                                            |
|----------------------------|-----------------------------------------------------------------|
| `spawn(coro, name=None)`   | `coro` をタスクとして開始し、終了するまでその参照を保持します    |
| `watch(callback)`          | 失敗したタスクごとに `callback(exc)` を呼び出します             |
| `await stop()`             | すべてのタスクをキャンセルし、すべての終了を待ちます。`disconnect()` から呼び出されます |

ワーカーやジョブの外（素の `async with DI` の下など）では、失敗はログに記録されるだけで、タスクは `disconnect()` の際にキャンセルされます。

## <a id="exit-codes"></a>終了コード

最初に一致したルールが適用されます。

| 条件                                                                                                | 終了コード     |
|-----------------------------------------------------------------------------------------------------|----------------|
| 無効なコマンドライン（`UsageError`）                                                                | `2`            |
| 例外：シグネチャ、クライアントの解決または接続、エントリーポイント、バックグラウンドタスクのいずれか | `1`            |
| 終了シグナルを受信した                                                                              | `128 + signum` |
| それ以外                                                                                            | `0`            |

SIGTERM では `143`、SIGINT では `130` になります。シャットダウンを検知して正常に戻ったジョブも、終了コードは `128 + signum` です。作業が中断されたので、スケジューラはそれを完了とみなしてはならないからです。

これらのコードは、プロセスを起動する側のためのものです。

```bash
python -m app.jobs.sync --day 2026-10-01
case $? in
  0)       echo "synced" ;;
  2)       echo "fix the command line, retrying will not help" ;;
  130|143) echo "interrupted, safe to run again" ;;
  *)       echo "failed, see the log" ;;
esac
```

## <a id="hooks"></a>フック

フックはすべての実行を観測します。メトリクスの送信やトレーシングのスパンの開始などに使えます。

```python
# app/jobs/report.py
from nuke_di import Run, job

from app.clients import Postgres


class Timer:
    async def on_start(self, run: Run) -> None:
        print(f"hook: {run.kind} {run.name} started")

    async def on_finish(self, run: Run) -> None:
        seconds = (run.finished_at - run.started_at).total_seconds()
        print(f"hook: exit code {run.exit_code} in {seconds:.1f}s, error: {run.error!r}")


@job(hooks=[Timer()])
async def report(pg: Postgres, limit: int = 10) -> None:
    print(f"report: top {limit} customers")
```

```console
$ python -m app.jobs.report --limit 3
hook: job app.jobs.report.report started
postgres: connected
report: top 3 customers
postgres: disconnected
hook: exit code 0 in 0.0s, error: None

$ python -m app.jobs.report --limit three
usage: python -m app.jobs.report [-h] [--limit LIMIT]
python -m app.jobs.report: error: argument --limit: invalid int value: 'three'
hook: job app.jobs.report.report started
Run app.jobs.report.report failed: argument --limit: invalid int value: 'three'
hook: exit code 2 in 0.0s, error: UsageError("argument --limit: invalid int value: 'three'")
```

`on_start` はクライアントが解決される前にリストの順に呼び出され、`on_finish` はクライアントが切断された後に逆順で呼び出されます。そのため `on_finish` からは、接続の失敗も含めた `Run` の最終状態を参照できます。

| `Run` のフィールド | 値                                                                     |
|--------------------|------------------------------------------------------------------------|
| `name`             | モジュールと関数。例：`app.jobs.report.report`                         |
| `kind`             | `"job"` または `"worker"`                                              |
| `started_at`       | UTC の `datetime`                                                      |
| `finished_at`      | UTC の `datetime`。`on_finish` の前に設定されます                      |
| `exit_code`        | プロセスの終了コード。`on_finish` の前に設定されます                   |
| `error`            | 実行を失敗させた例外（`UsageError` など）、または `None`               |
| `signal`           | 最初に受信した終了シグナル、または `None`                              |
| `clients`          | クライアントごとの `ClientTiming`：接続と切断の時間と結果。接続前に実行が失敗した場合は空 |

フックはクライアントではなく普通のオブジェクトで、自身のリソースは自分で管理します。フック内で発生した例外はログに記録され、終了コードには影響しません。`--help` は実行ではないので、フックからは見えません。

### <a id="startup-metrics-and-structured-logs"></a>起動メトリクスと構造化ログ

起動メトリクスをエクスポートするのは `run.clients` です。`on_finish` では、各クライアントの接続と切断に
かかった時間がわかります。さらに `nuke_di` のすべてのログレコードには構造化フィールドが付くので、
JSON フォーマッターはメッセージを解析せずにクライアント単位で絞り込みや集計ができます：

```python
# app/jobs/startup.py
import json
import logging

from nuke_di import Run, job

from app.clients import Postgres, Warehouse


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {key: getattr(record, key) for key in ("run", "client", "layer", "duration") if hasattr(record, key)}
        return json.dumps({"level": record.levelname, "message": record.getMessage(), **fields})


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])


class StartupMetrics:
    async def on_start(self, run: Run) -> None:
        pass

    async def on_finish(self, run: Run) -> None:
        for client in run.clients:
            print(f"metric: {client.name} connect={client.connect:.3f}s {client.connect_outcome}")


@job(hooks=[StartupMetrics()])
async def startup(pg: Postgres, warehouse: Warehouse) -> None:
    print("startup: done")
```

```console
$ python -m app.jobs.startup
{"level": "INFO", "message": "Starting job app.jobs.startup.startup", "run": "app.jobs.startup.startup"}
postgres: connected
warehouse: connected
{"level": "INFO", "message": "Connected 4 clients in 1 layer in 0.00s (slowest: Postgres 0.00s, Shutdown 0.00s, Warehouse 0.00s)", "run": "app.jobs.startup.startup", "duration": 0.00015945796621963382}
startup: done
postgres: disconnected
warehouse: disconnected
{"level": "INFO", "message": "Run app.jobs.startup.startup finished with exit code 0 in 0.001s", "run": "app.jobs.startup.startup", "duration": 0.001171}
metric: Shutdown connect=0.000s ok
metric: BackgroundTasks connect=0.000s ok
metric: Postgres connect=0.000s ok
metric: Warehouse connect=0.000s ok
```

| フィールド | 付くレコード                                                                  |
|------------|-------------------------------------------------------------------------------|
| `run`      | ワーカーやジョブの内側で出るすべてのレコード（コンテナのものを含む）：実行の名前 |
| `client`   | 1 つのクライアントに関するすべてのレコード：解決、接続、切断、失敗            |
| `layer`    | クライアントの接続や切断に関するすべてのレコードと `Connecting layer`         |
| `duration` | 秒数：接続または切断したクライアント、起動の要約、終了した実行                |

どの実行も自分の `Shutdown` と `BackgroundTasks` クライアントを接続するので、それらも
`run.clients` と要約に現れます。

## <a id="running-in-kubernetes"></a>Kubernetes での実行

ジョブは CronJob に、ワーカーは Deployment に対応します。ワーカーには、[シャットダウンにかかる時間](#grace-period)をまかなえるだけの `terminationGracePeriodSeconds` を設定してください。

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: report
spec:
  schedule: "0 6 * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: report
              image: registry.example.com/app:1.0
              command: ["python", "-m", "app.jobs.report"]
              args: ["--limit", "20"]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: consumer
spec:
  replicas: 2
  selector:
    matchLabels: {app: consumer}
  template:
    metadata:
      labels: {app: consumer}
    spec:
      terminationGracePeriodSeconds: 30  # >= SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers
      containers:
        - name: consumer
          image: registry.example.com/app:1.0
          command: ["python", "-m", "app.workers.consumer"]
          env:
            - {name: SHUTDOWN_GRACE_SECONDS, value: "15"}
```

単発のバックフィルは、同じイメージを別のパラメータで実行するだけです。

```bash
kubectl run sync-backfill --rm -it --restart=Never --image=registry.example.com/app:1.0 \
  --command -- python -m app.jobs.sync --day 2026-09-30 --mode FULL
```
