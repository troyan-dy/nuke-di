# <a id="taskiq"></a>taskiq

[English](../../guide/taskiq.md) · [Русский](../ru/taskiq.md) · [简体中文](../zh-CN/taskiq.md) · [Español](../es/taskiq.md) · [Português (Brasil)](../pt-BR/taskiq.md) · [日本語](../ja/taskiq.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

Zadanie taskiq przyjmuje klienta po adnotacji typu, obok argumentów, z którymi zostało wysłane:

```bash
pip install "nuke-di[taskiq]"
```

Wymaga taskiq 0.11 lub nowszego, z dowolnym brokerem. Z klientami z przykładów dla [FastAPI](fastapi.md)
i z `InMemoryBroker`, który wykonuje zadania w tym samym procesie, który je wysyła:

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

**Sprawdzanie typów.** taskiq typuje `.kiq()` sygnaturą samego zadania, więc mypy i pyright domagają się
`users` w `send_report.kiq(42)`; dotyczy to każdego argumentu, który wypełnia taskiq, z `Annotated` czy bez.
Wartość domyślna sprawia, że dla nich argument staje się opcjonalny, a klient nadal przychodzi z kontenera
po swoim typie:

```python
@broker.task
async def send_report(user_id: int, users: UserService = TaskiqDepends()) -> None:
    print(await users.greet(user_id))
```

Reguła `B008` Ruffa zgłasza wywołanie w wartości domyślnej; znaczniki taskiq są tam bezpieczne:

```toml
# pyproject.toml
[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["taskiq.TaskiqDepends"]
```

**Prawdziwy worker.** We wdrożeniu broker należy do kolejki, np. `NatsBroker` z
[taskiq-nats](https://github.com/taskiq-python/taskiq-nats); nic więcej się nie zmienia:

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

Klientów łączy proces workera; proces, który tylko wysyła zadania, np. aplikacja webowa, łączy broker i nic
więcej:

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

W innym terminalu:

```console
$ python -m app.kick
kicked send_report(42)
```

Dwie minuty przed `Stopping prefetching messages...` to sprawa taskiq: jego proces workera (taskiq 0.13
z taskiq-nats 0.7) zauważa sygnał przy następnej wiadomości albo pingu NATS.

Zasady:

- **Gdzie wstawiani są klienci.** W argumentach zadań brokera i każdej funkcji `TaskiqDepends(...)`, której
  używają, na dowolnej głębokości, łącznie z zależnościami-generatorami. Każdy inny argument należy do
  taskiq: argumenty `.kiq()`, `Context`, `TaskiqState`. Klasę-zależność, `Annotated[Auth, TaskiqDepends()]`,
  taskiq buduje z jej własnego `__init__`, którego nuke-di nie przepisuje: klasa, która przyjmuje klientów,
  sama jest klientem albo dostaje ich przez funkcję-zależność.
- **Którzy klienci startują.** Klienci każdego zadania z `broker.get_all_tasks()`: własnych zadań brokera
  i współdzielonych (`@shared_task`). Zadania można deklarować przed `setup(broker)` albo po nim,
  współdzielone tylko przed: rejestrują się we współdzielonym brokerze taskiq, do którego `setup()` się nie
  podpina.
- **Który proces łączy.** Ten, w którym broker wywołuje `WORKER_STARTUP`: proces `taskiq worker` oraz każdy
  proces, który startuje `InMemoryBroker`, będący swoim własnym workerem. Proces, który tylko wysyła
  zadania, startuje broker z `CLIENT_STARTUP` i nie łączy żadnego klienta. Dzięki temu jeden moduł
  z brokerem służy obu: worker łączy przez taskiq, aplikacja webowa przez własną integrację.
- **Lifespan.** Klienci łączą się przed pozostałymi handlerami `WORKER_STARTUP`, także tymi
  zarejestrowanymi przed `setup()`, i rozłączają się, gdy `broker.shutdown()` wykona handlery
  `WORKER_SHUTDOWN`, middleware i backend wyników. Nieudany `connect()` przerywa `broker.startup()`, a wraz
  z nim worker. `Shutdown` i `BackgroundTasks` działają tak jak w [FastAPI](fastapi.md).
- **Instancje.** Tak jak w `inject()`, `Client` to jedna instancja na kontener, a `NotSingletonClient` —
  jedna instancja na każdy argument, który go deklaruje, a nie jedna na zadanie.
- **Funkcja pozostaje funkcją.** Jej sygnatura pokazuje taskiq `Annotated[UserService, TaskiqDepends(...)]`,
  tak jak w [FastAPI](fastapi.md); `await send_report(1, users)` wywołuje ją z klientami podanymi ręcznie.
- **Kontener łączy się raz.** `InMemoryBroker` wystartowany w procesie, w którym kontener jest już
  połączony, np. wewnątrz aplikacji FastAPI działającej na tym samym `DI`, kończy się błędem
  `RuntimeError: nuke-di clients failed to start: the container is already connected`. Daj takiemu
  brokerowi własny kontener: `setup(broker, container=Dependencies())`. Funkcja-zadanie obsługuje jeden
  broker naraz, tak jak subscriber FastStream.

**Testowanie.** Wykonuj zadania na `InMemoryBroker` i startuj go wewnątrz override:

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

Zadanie wykonane bez startu workera, np. wysłane do `InMemoryBroker`, którego nigdy nie wystartowano, kończy
się błędem ``RuntimeError: UserService is not connected: the clients connect when the worker starts; run tasks
with `taskiq worker`, or start an InMemoryBroker with `await broker.startup()` before kicking them`` w swoim
wyniku. Zadanie zadeklarowane po starcie workera kończy się błędem
`RuntimeError: UserService was not started with the worker`.
