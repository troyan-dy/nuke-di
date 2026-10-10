# <a id="taskiq"></a>taskiq

[English](../../guide/taskiq.md) · **Русский** · [简体中文](../zh-CN/taskiq.md) · [Español](../es/taskiq.md) · [Português (Brasil)](../pt-BR/taskiq.md) · [日本語](../ja/taskiq.md) · [Polski](../pl/taskiq.md)

← [Документация](../README.ru.md#documentation)

Задача taskiq получает клиент по аннотации типа рядом с аргументами, с которыми её отправили:

```bash
pip install "nuke-di[taskiq]"
```

Нужен taskiq 0.11 или новее, с любым брокером. С клиентами из примеров для [FastAPI](fastapi.md) и с
`InMemoryBroker`, который выполняет задачи в том же процессе, что их отправляет:

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

**Проверка типов.** taskiq типизирует `.kiq()` сигнатурой самой задачи, поэтому mypy и pyright требуют
`users` в `send_report.kiq(42)`; то же касается любого аргумента, который заполняет taskiq, с `Annotated` или
без. Значение по умолчанию делает аргумент необязательным для них, а клиент всё так же приходит из
контейнера по своему типу:

```python
@broker.task
async def send_report(user_id: int, users: UserService = TaskiqDepends()) -> None:
    print(await users.greet(user_id))
```

Правило `B008` в Ruff ругается на вызов в значении по умолчанию; маркерам taskiq там самое место:

```toml
# pyproject.toml
[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["taskiq.TaskiqDepends"]
```

**Настоящий воркер.** В развёртывании брокер — это брокер очереди, например `NatsBroker` из
[taskiq-nats](https://github.com/taskiq-python/taskiq-nats); больше ничего не меняется:

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

Клиенты подключает процесс воркера; процесс, который только отправляет задачи, например веб-приложение,
подключает брокер и больше ничего:

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

В другом терминале:

```console
$ python -m app.kick
kicked send_report(42)
```

Две минуты до `Stopping prefetching messages...` — на совести taskiq: его процесс воркера (taskiq 0.13 с
taskiq-nats 0.7) замечает сигнал при следующем сообщении или ping от NATS.

Правила:

- **Где заполняются клиенты.** В аргументах задач брокера и всех функций `TaskiqDepends(...)`, которые они
  используют, на любой глубине, включая зависимости-генераторы. Все остальные аргументы достаются taskiq:
  аргументы `.kiq()`, `Context`, `TaskiqState`. Класс-зависимость, `Annotated[Auth, TaskiqDepends()]`,
  taskiq создаёт по его собственному `__init__`, который nuke-di не переписывает, поэтому класс, чей
  `__init__` принимает клиенты, отклоняется при регистрации задачи: `TypeError: Auth takes clients in
  __init__ and is a taskiq dependency`. Клиент берите по аннотации типа, без `TaskiqDepends()`, а классу,
  которому нужны клиенты, передавайте их через функцию-зависимость.
- **Какие клиенты стартуют.** Клиенты всех задач из `broker.get_all_tasks()`: собственных задач брокера и
  общих (`@shared_task`). Задачи брокера можно объявлять до или после `setup(broker)`. Общая задача
  регистрируется в общем брокере taskiq, к которому `setup()` не подключается: `setup()` переписывает общие
  задачи, которые уже есть, а старт воркера — остальные. `taskiq worker` читает сигнатуры своих задач до
  старта, поэтому там общую задачу импортируют до `setup()`; `InMemoryBroker` читает её при первом запуске,
  поэтому там подходит любой порядок.
- **Какой процесс подключает.** Тот, в котором брокер запускает `WORKER_STARTUP`: процесс `taskiq worker`
  и любой процесс, который стартует `InMemoryBroker`, сам себе воркер. Процесс, который только отправляет
  задачи, стартует брокер с `CLIENT_STARTUP` и не подключает ни одного клиента. Так один модуль с брокером
  служит обоим: воркер подключается через taskiq, веб-приложение — через свою интеграцию.
- **Lifespan.** Клиенты подключаются раньше остальных обработчиков `WORKER_STARTUP`, в том числе
  зарегистрированных до `setup()`, и отключаются после того, как `broker.shutdown()` выполнил обработчики
  `WORKER_SHUTDOWN`, middleware и бэкенд результатов; на `InMemoryBroker` — ещё и после того, как
  закончатся задачи, которые всё ещё выполняются. `Shutdown` и `BackgroundTasks` ведут себя так же, как в
  [FastAPI](fastapi.md). `taskiq worker` даёт `broker.shutdown()` `--shutdown-timeout` секунд, по умолчанию
  5, а каждый `disconnect()` может занять `DISCONNECT_TIMEOUT_SECONDS`, по умолчанию 10: ставьте
  `--shutdown-timeout` больше самой длинной цепочки отключений, иначе медленное отключение оборвётся на
  середине.
- **Неудачный старт.** Упавший `connect()` проваливает `broker.startup()` с `RuntimeError`, и процесс
  воркера умирает. Менеджер процессов taskiq по умолчанию перезапускает его бесконечно (`--max-fails -1`),
  так что оркестратор никогда не видит падения, а недоступную зависимость дёргают каждую секунду. Запускайте
  воркер с `--max-fails 1`: тогда он завершается с кодом 255, и оркестратор перезапускает его со своим
  backoff, ведь `connect()` в nuke-di fail-fast.
- **Экземпляры.** Как и с `inject()`, `Client` — это один экземпляр на контейнер, а `NotSingletonClient` —
  один экземпляр на каждый объявляющий его аргумент, а не на каждую задачу.
- **Функция остаётся функцией.** Её сигнатура показывает taskiq `Annotated[UserService, TaskiqDepends(...)]`,
  как в [FastAPI](fastapi.md); `await send_report(1, users)` вызывает её с клиентами, переданными вручную.
  Функция, которая принимает клиенты, обслуживает либо taskiq, либо FastAPI: уже связанная с FastAPI
  отклоняется с `TypeError` до регистрации задачи.
- **Контейнер подключается один раз.** `InMemoryBroker`, запущенный в процессе, где контейнер уже подключён,
  например внутри приложения FastAPI на том же `DI`, падает с `RuntimeError: nuke-di clients failed to
  start: the container is already connected`. Дайте такому брокеру собственный контейнер:
  `setup(broker, container=Dependencies())`. По той же причине воркер не стартует с
  `taskiq_fastapi.init(broker, app)` для приложения, настроенного через `nuke_di.fastapi` на том же
  контейнере: он входит в lifespan приложения на `WORKER_STARTUP`. С `nuke_di.taskiq` задачи получают клиенты
  и без него. Функция-задача обслуживает один брокер за раз, как и подписчик FastStream.

**Тестирование.** Выполняйте задачи на `InMemoryBroker` и запускайте его внутри override:

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

Задача, выполненная без старта воркера, например отправленная в `InMemoryBroker`, который так и не
запустили, завершается ошибкой ``RuntimeError: UserService is not connected: the clients connect when the
worker starts; run tasks with `taskiq worker`, or start an InMemoryBroker with `await broker.startup()` before
kicking them`` в своём результате. Задача, объявленная после старта воркера, завершается ошибкой
`RuntimeError: UserService was not started with the worker`.
