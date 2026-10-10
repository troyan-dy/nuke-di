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

- **クライアントが埋められる場所。** ブローカーのタスクと、それらが使うすべての `TaskiqDepends(...)` 関数の引数です。深さは問わず、ジェネレーターの依存関係も含みます。それ以外の引数（`.kiq()` の引数、`Context`、`TaskiqState`）はすべて taskiq が扱います。依存関係のクラス `Annotated[Auth, TaskiqDepends()]` は、taskiq がそのクラス自身の `__init__` から組み立てます。nuke-di はこの `__init__` を書き換えないので、クライアントを受け取るクラスはそれ自体をクライアントにするか、依存関係の関数を通してクライアントを受け取ります。
- **起動するクライアント。** `broker.get_all_tasks()` に含まれるすべてのタスク、つまりブローカー自身のタスクと共有タスク（`@shared_task`）のクライアントです。タスクは `setup(broker)` の前に宣言しても後に宣言してもかまいませんが、共有タスクは前に宣言する必要があります。共有タスクは taskiq の共有ブローカーに登録され、`setup()` はそこにはフックしないからです。
- **接続するプロセス。** ブローカーが `WORKER_STARTUP` を発火するプロセスです。つまり `taskiq worker` のプロセスと、自分自身がワーカーである `InMemoryBroker` を起動するすべてのプロセスです。タスクをキックするだけのプロセスは `CLIENT_STARTUP` でブローカーを起動し、クライアントは 1 つも接続しません。そのため、ブローカーを置いた 1 つのモジュールを両方で使えます。ワーカーは taskiq を通して、Web アプリは自分のインテグレーションを通して接続します。
- **lifespan。** クライアントは、ほかの `WORKER_STARTUP` ハンドラー（`setup()` より前に登録されたものも含む）より先に接続し、`broker.shutdown()` が `WORKER_SHUTDOWN` ハンドラー、ミドルウェア、結果バックエンドを実行し終えた後に切断します。`connect()` が失敗すると `broker.startup()` が失敗し、ワーカーも失敗します。`Shutdown` と `BackgroundTasks` は [FastAPI](fastapi.md) と同じように動作します。
- **インスタンス。** `inject()` と同様に、`Client` はコンテナごとに 1 インスタンス、`NotSingletonClient` はそれを宣言する引数ごとに 1 インスタンスです。タスクごとではありません。
- **関数は関数のまま。** [FastAPI](fastapi.md) と同じように、taskiq から見たシグネチャは `Annotated[UserService, TaskiqDepends(...)]` になります。`await send_report(1, users)` と書けば、手で渡したクライアントで呼び出せます。
- **コンテナの接続は一度だけ。** コンテナがすでに接続されているプロセス（たとえば同じ `DI` で動く FastAPI アプリの中）で起動した `InMemoryBroker` は、`RuntimeError: nuke-di clients failed to start: the container is already connected` で失敗します。そのブローカーには専用のコンテナを渡してください：`setup(broker, container=Dependencies())`。FastStream のサブスクライバーと同じく、タスク関数が一度に扱えるブローカーは 1 つです。

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
        task = await send_report.kiq(1)
        await task.wait_result()
        await broker.shutdown()

    assert "Hello, alice!" in capsys.readouterr().out
```

```console
$ pytest -q tests/test_tasks.py
.                                                                        [100%]
1 passed in 0.38s
```

ワーカーの起動を経ずに実行されたタスク（一度も起動していない `InMemoryBroker` にキックしたタスクなど）は、結果に ``RuntimeError: UserService is not connected: the clients connect when the worker starts; run tasks with `taskiq worker`, or start an InMemoryBroker with `await broker.startup()` before kicking them`` が入って失敗します。ワーカーの起動後に宣言されたタスクは `RuntimeError: UserService was not started with the worker` で失敗します。
