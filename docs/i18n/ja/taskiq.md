# <a id="taskiq"></a>taskiq

[English](../../guide/taskiq.md) · [Русский](../ru/taskiq.md) · [简体中文](../zh-CN/taskiq.md) · [Español](../es/taskiq.md) · [Português (Brasil)](../pt-BR/taskiq.md) · **日本語** · [Polski](../pl/taskiq.md)

← [ドキュメント](../README.ja.md#documentation)

taskiq のタスクは、キックされたときの引数と並んで、型ヒントでクライアントを受け取ります。

```bash
pip install "nuke-di[taskiq]"
```

taskiq 0.11 以降が必要で、ブローカーの種類は問いません。[FastAPI](fastapi.md) の例と同じクライアントと、タスクをキックしたプロセスの中でそのまま実行する `InMemoryBroker` を使います。

```python
# app/tasks.py
from typing import Annotated

from taskiq import Context, InMemoryBroker, TaskiqDepends

from app.clients import Database, UserService
from nuke_di.taskiq import setup

broker = InMemoryBroker()  # runs the tasks in this process
setup(broker)  # clients connect when the worker starts, disconnect when it stops


@broker.task
async def send_report(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))


async def account_name(context: Annotated[Context, TaskiqDepends()], db: Database) -> str:
    # A dependency gets taskiq's own objects and clients side by side
    return await db.fetch_user(context.message.kwargs["account_id"])


@broker.task
async def close_account(account_id: int, name: Annotated[str, TaskiqDepends(account_name)]) -> str:
    return f"closed the account of {name}"
```

```python
# app/main.py
import asyncio

from app.tasks import broker, close_account, send_report


async def main() -> None:
    # An InMemoryBroker is its own worker: its startup connects the clients
    await broker.startup()
    report = await send_report.kiq(42)
    await report.wait_result()
    closed = await close_account.kiq(account_id=7)
    print((await closed.wait_result()).return_value)
    await broker.shutdown()


asyncio.run(main())
```

```console
$ python -m app.main
database: connected
Hello, user-42!
closed the account of user-7
database: disconnected
```

**型チェッカー。** taskiq は `.kiq()` の型をタスク自身のシグネチャで付けるため、mypy と pyright は `send_report.kiq(42)` に `users` も渡すよう求めます。これは `Annotated` の有無にかかわらず、taskiq が埋めるすべての引数に共通です。デフォルト値を付けると型チェッカーにとってその引数は省略可能になり、クライアントは引き続き型によってコンテナから渡されます。

```python
@broker.task
async def send_report(user_id: int, users: UserService = TaskiqDepends()) -> None:
    print(await users.greet(user_id))
```

Ruff の `B008` はデフォルト値の中の関数呼び出しを警告しますが、taskiq のマーカーはそこに置いても安全です。

```toml
# pyproject.toml
[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["taskiq.TaskiqDepends"]
```

**本物のワーカー。** 本番環境では、ブローカーはキューのブローカーです。たとえば [taskiq-nats](https://github.com/taskiq-python/taskiq-nats) の `NatsBroker` で、それ以外は何も変わりません。

```python
# app/tasks.py
import os

from taskiq_nats import NatsBroker

from app.clients import UserService
from nuke_di.taskiq import setup

broker = NatsBroker(os.environ.get("NATS_URL", "nats://localhost:4222"), queue="reports")
setup(broker)


@broker.task
async def send_report(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))
```

クライアントを接続するのはワーカープロセスです。タスクをキックするだけのプロセス（Web アプリなど）は、ブローカーだけを接続し、それ以外は何も接続しません。

```python
# app/kick.py
import asyncio

from app.tasks import broker, send_report


async def main() -> None:
    # A client process: the broker connects to NATS, the clients stay unconnected
    await broker.startup()
    await send_report.kiq(42)
    print("kicked send_report(42)")
    await broker.shutdown()


asyncio.run(main())
```

```console
$ taskiq worker app.tasks:broker --workers 1
[2026-10-10 18:40:00,853][taskiq.worker][INFO   ][MainProcess] Pid of a main process: 56250
[2026-10-10 18:40:00,853][taskiq.worker][INFO   ][MainProcess] Starting 1 worker processes.
[2026-10-10 18:40:00,859][taskiq.process-manager][INFO   ][MainProcess] Started process worker-0 with pid 56252
database: connected
[2026-10-10 18:40:01,084][nuke_di.core][INFO   ][worker-0] Connected 2 clients in 0.00s (slowest: Database 0.00s, UserService 0.00s)
[2026-10-10 18:40:01,091][taskiq.receiver.receiver][INFO   ][worker-0] Listening started.
[2026-10-10 18:40:03,815][taskiq.receiver.receiver][INFO   ][worker-0] Executing task app.tasks:send_report with ID: e987ddad37444fa5a0ade0174578c9f2
Hello, user-42!
^C
[2026-10-10 18:40:05,845][taskiq.process-manager][INFO   ][MainProcess] Workers are scheduled for shutdown.
[2026-10-10 18:40:05,995][taskiq.process-manager][INFO   ][MainProcess] Stopped process worker-0 with pid 56252
[2026-10-10 18:42:01,140][taskiq.receiver.receiver][INFO   ][worker-0] Stopping prefetching messages...
[2026-10-10 18:42:01,143][taskiq.receiver.receiver][INFO   ][worker-0] The runner is stopped.
[2026-10-10 18:42:01,144][taskiq.worker][INFO   ][worker-0] Shutting down the broker.
database: disconnected
```

別のターミナルで：

```console
$ python -m app.kick
kicked send_report(42)
```

`Stopping prefetching messages...` までの 2 分間は taskiq 側の事情です。taskiq のワーカープロセス（taskiq 0.13 と taskiq-nats 0.7）は、次のメッセージか NATS の ping が来たときにシグナルに気づきます。

ルール：

- **クライアントが埋められる場所。** ブローカーのタスクと、それらが使うすべての `TaskiqDepends(...)` 関数の引数です。深さは問わず、ジェネレーターの依存関係も含みます。それ以外の引数（`.kiq()` の引数、`Context`、`TaskiqState`）はすべて taskiq が扱います。依存関係のクラス `Annotated[Auth, TaskiqDepends()]` は、taskiq がそのクラス自身の `__init__` から組み立て、nuke-di はこの `__init__` を書き換えません。そのため `__init__` でクライアントを受け取るクラスは、タスクの登録時に `TypeError: Auth takes clients in __init__ and is a taskiq dependency` で拒否されます。クライアントは `TaskiqDepends()` を付けずに型ヒントで受け取り、クライアントが必要なクラスには依存関係の関数を通して渡してください。
- **起動するクライアント。** `broker.get_all_tasks()` に含まれるすべてのタスク、つまりブローカー自身のタスクと共有タスク（`@shared_task`）のクライアントです。ブローカーのタスクは `setup(broker)` の前に宣言しても後に宣言してもかまいません。共有タスクは taskiq の共有ブローカーに登録され、`setup()` はそこにはフックしません。`setup()` はその時点の共有タスクを書き換え、残りはワーカーの起動時に書き換えます。`taskiq worker` は起動前にタスクのシグネチャを読むので、そこでは共有タスクを `setup()` より前にインポートします。`InMemoryBroker` は最初の実行時に読むので、そこではどちらの順序でもかまいません。
- **接続するプロセス。** ブローカーが `WORKER_STARTUP` を発火するプロセスです。つまり `taskiq worker` のプロセスと、自分自身がワーカーである `InMemoryBroker` を起動するすべてのプロセスです。タスクをキックするだけのプロセスは `CLIENT_STARTUP` でブローカーを起動し、クライアントは 1 つも接続しません。そのため、ブローカーを置いた 1 つのモジュールを両方で使えます。ワーカーは taskiq を通して、Web アプリは自分のインテグレーションを通して接続します。
- **lifespan。** クライアントは、ほかの `WORKER_STARTUP` ハンドラー（`setup()` より前に登録されたものも含む）より先に接続し、`broker.shutdown()` が `WORKER_SHUTDOWN` ハンドラー、ミドルウェア、結果バックエンドを実行し終えた後に切断します。`InMemoryBroker` では、まだ実行中のタスクが終わるのも待ちます。`Shutdown` と `BackgroundTasks` は [FastAPI](fastapi.md) と同じように動作します。`taskiq worker` が `broker.shutdown()` に与える時間は `--shutdown-timeout` 秒（デフォルト 5）で、各 `disconnect()` は `DISCONNECT_TIMEOUT_SECONDS`（デフォルト 10）までかかり得ます。`--shutdown-timeout` は最も長い切断の連鎖より大きくしてください。そうしないと、遅い切断が途中で打ち切られます。
- **起動の失敗。** `connect()` が失敗すると `broker.startup()` が `RuntimeError` で失敗し、ワーカープロセスは終了します。taskiq のプロセスマネージャーはデフォルトでそれを無限に再起動する（`--max-fails -1`）ため、オーケストレーターはクラッシュに気づかず、落ちている依存先に毎秒アクセスし続けます。ワーカーは `--max-fails 1` で実行してください。そうすると終了コード 255 で終了し、オーケストレーターが自身のバックオフで再起動します。nuke-di の `connect()` はフェイルファストだからです。
- **インスタンス。** `inject()` と同様に、`Client` はコンテナごとに 1 インスタンス、`NotSingletonClient` はそれを宣言する引数ごとに 1 インスタンスです。タスクごとではありません。
- **関数は関数のまま。** [FastAPI](fastapi.md) と同じように、taskiq から見たシグネチャは `Annotated[UserService, TaskiqDepends(...)]` になります。`await send_report(1, users)` と書けば、手で渡したクライアントで呼び出せます。クライアントを受け取る関数が扱えるのは taskiq か FastAPI のどちらか一方です。すでに FastAPI にバインドされた関数は、タスクが登録される前に `TypeError` で拒否されます。
- **コンテナの接続は一度だけ。** コンテナがすでに接続されているプロセス（たとえば同じ `DI` で動く FastAPI アプリの中）で起動した `InMemoryBroker` は、`RuntimeError: nuke-di clients failed to start: the container is already connected` で失敗します。そのブローカーには専用のコンテナを渡してください：`setup(broker, container=Dependencies())`。同じ理由で、同じコンテナ上で `nuke_di.fastapi` を設定したアプリに対して `taskiq_fastapi.init(broker, app)` を使うと、ワーカーの起動が失敗します。これは `WORKER_STARTUP` でアプリの lifespan に入るためです。`nuke_di.taskiq` があれば、それがなくてもタスクはクライアントを受け取れます。FastStream のサブスクライバーと同じく、タスク関数が一度に扱えるブローカーは 1 つです。

**テスト。** タスクを `InMemoryBroker` で実行し、override の内側でブローカーを起動します。

```python
# tests/test_tasks.py
import pytest

from app.clients import Database
from app.tasks import broker, send_report
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_send_report(capsys: pytest.CaptureFixture[str]) -> None:
    with DI.override(Database, FakeDatabase()):
        await broker.startup()
        try:
            task = await send_report.kiq(1)
            await task.wait_result()
        finally:
            await broker.shutdown()

    assert "Hello, alice!" in capsys.readouterr().out
```

```console
$ pytest -q tests/test_tasks.py
.                                                                        [100%]
1 passed in 0.44s
```

ワーカーの起動を経ずに実行されたタスク（一度も起動していない `InMemoryBroker` にキックしたタスクなど）は、結果に ``RuntimeError: UserService is not connected: the clients connect when the worker starts; run tasks with `taskiq worker`, or start an InMemoryBroker with `await broker.startup()` before kicking them`` が入って失敗します。ワーカーの起動後に宣言されたタスクは `RuntimeError: UserService was not started with the worker` で失敗します。
