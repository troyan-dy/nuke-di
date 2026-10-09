# <a id="workers-and-jobs"></a>Воркеры и джобы

[English](../../guide/workers-and-jobs.md) · **Русский** · [简体中文](../zh-CN/workers-and-jobs.md) · [Español](../es/workers-and-jobs.md) · [Português (Brasil)](../pt-BR/workers-and-jobs.md) · [日本語](../ja/workers-and-jobs.md) · [Polski](../pl/workers-and-jobs.md)

← [Документация](../README.ru.md#documentation)

Асинхронная функция становится основной программой процесса с помощью одного декоратора:

| Декоратор | Сколько работает                                              |
|-----------|---------------------------------------------------------------|
| `@job`    | Один раз: процесс завершается, когда функция возвращает управление |
| `@worker` | Пока процесс не получит SIGTERM или SIGINT                    |

Примеры в этом разделе используют общий модуль клиентов:

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

## <a id="your-first-job"></a>Первая джоба

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

Это вся программа: ни `main()`, ни `asyncio.run()`, ни `if __name__ == "__main__"`.
Процесс разрешает клиенты из глобального контейнера `DI`, подключает их, выполняет
функцию, отключает клиенты и завершается с [кодом завершения](#exit-codes). Расписание — не забота
библиотеки: когда запускать джобу, решает Kubernetes CronJob, таймер systemd или crontab.

`nuke-di` пишет в лог каждый запуск через логгер `nuke_di`. Чтобы увидеть эти записи, настройте
логирование выше декоратора:

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
INFO  nuke_di.core: Connected 4 clients in 0.00s (slowest: Postgres 0.00s, Shutdown 0.00s, Warehouse 0.00s)
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
INFO  nuke_di.run: Run app.jobs.sync.sync finished with exit code 0 in 0.002s
```

### <a id="one-entrypoint-per-module-defined-last"></a>Одна точка входа на модуль, и она последняя

Когда модуль запущен как `__main__`, декоратор сразу выполняет функцию, и на этом процесс
завершается:

```python
# app/jobs/sync.py
DI.mock(Warehouse, FakeWarehouse())  # runs: code above the decorator is fine


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None: ...


print("never printed")  # never runs under `python -m app.jobs.sync`
```

**Держите одну точку входа на модуль и определяйте её последней.** При обычном импорте, например
из теста, декоратор возвращает функцию без изменений, и ничего не запускается. Декорируемая функция
должна быть объявлена через `async def`, иначе при импорте возникнет `TypeError`.

## <a id="parameters"></a>Параметры

Каждый аннотированный аргумент, который не является клиентом, становится опцией командной строки.
Вот та же джоба, которая теперь умеет копировать любой день, выбранные таблицы и делать пробный прогон
(dry run):

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

`pg` и `warehouse` — клиенты, они внедряются; `day`, `tables`, `mode` и `dry_run` берутся
из командной строки:

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

`--help` генерируется из сигнатуры и docstring. Он ничего не подключает
(Python 3.13+ выводит `-d, --day DAY` вместо `-d DAY, --day DAY`):

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

Неверная командная строка отклоняется **до того, как будет разрешён или подключён хоть один клиент**,
с кодом завершения `2`:

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

Первые две строки каждой ошибки выводит `argparse`; строка `Run ... failed` — это запись уровня
`ERROR` логгера `nuke_di`, поэтому она подчиняется вашей настройке логирования.
Сокращения не принимаются: `--dry` не считается `--dry-run`.

### <a id="supported-types"></a>Поддерживаемые типы

| Аннотация                                     | Командная строка                | Пример                          |
|-----------------------------------------------|---------------------------------|---------------------------------|
| `str`, `int`, `float`, `pathlib.Path`         | `--name VALUE`                  | `--limit 10`                    |
| `bool`                                        | `--name` / `--no-name`          | `--dry-run`                     |
| `datetime.date`, `datetime.datetime`          | ISO 8601                        | `--since 2026-10-01T12:00:00`   |
| `Enum`                                        | **имя** члена, как в коде       | `--mode FULL`                   |
| `list[T]` любого из типов выше, кроме `bool`  | повторяющаяся опция             | `--table users --table orders`  |
| `T \| None`                                   | как `T`                         | `--limit 10`                    |

Правила:

- **Имя.** Опция называется по аргументу, `_` заменяется на `-`:
  `dry_run` становится `--dry-run`. Позиционных аргументов нет, поэтому новый параметр никогда
  не ломает существующую командную строку.
- **Обязательная или нет.** Аргумент без значения по умолчанию — обязательная опция. Аргумент
  со значением по умолчанию необязателен, и если опцию не указали, используется значение по умолчанию
  самой функции.
- **`Option`.** `Annotated[T, Option(help=..., short=...)]` добавляет текст справки и однобуквенный
  псевдоним вроде `-d`. Оба необязательны.
- **Без параметров.** Точка входа без параметров всё равно разбирает командную строку: она
  отвечает на `--help` и отклоняет любой аргумент с кодом завершения `2`.

Такие сигнатуры — ошибка в коде, а не в командной строке. Они завершают запуск с
`InvalidSignatureError` и кодом завершения `1`:

```python
async def sync(day: dict[str, int]) -> None: ...  # unsupported type
async def sync(pg: Annotated[Postgres, Option(help="...")]) -> None: ...  # Option on a client
async def sync(help: bool = False) -> None: ...  # clashes with --help
async def sync(day: int, /) -> None: ...  # positional-only
```

### <a id="parameters-in-tests"></a>Параметры в тестах

Декорированная функция остаётся обычной корутиной, поэтому тест передаёт параметры как именованные
аргументы:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    warehouse.changes.assert_awaited_once_with("users", datetime.date(2026, 10, 1))
    pg.upsert.assert_awaited_once_with("users", ["row"])
```

## <a id="your-first-worker"></a>Первый воркер

Воркер работает, пока процесс не попросят остановиться. Он зависит от клиента `Shutdown`, который
взводится при первом SIGTERM или SIGINT, и доделывает текущую порцию работы:

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

Ctrl+C посреди третьего сообщения: сообщение обрабатывается до конца, цикл завершается,
клиенты отключаются.

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

У `Shutdown` три метода:

| Метод            | Описание                                                                  |
|------------------|---------------------------------------------------------------------------|
| `is_set()`       | Началась ли остановка; проверяйте между порциями работы                   |
| `await wait()`   | Ждать, пока не начнётся остановка                                         |
| `set()`          | Начать остановку вручную, например в тесте                                |

Вне воркера или джобы его никто не взводит, поэтому цикл, зависящий от `Shutdown`, без изменений
работает и внутри веб-приложения. Воркер, который сам вернул управление или выбросил исключение,
тоже завершает процесс: перезапускать его — забота оркестратора.

В Windows обрабатывается только SIGINT (Ctrl+C); у SIGTERM остаётся поведение по умолчанию.

## <a id="grace-period"></a>Grace period

Воркер, который игнорирует `Shutdown`, отменяется через `SHUTDOWN_GRACE_SECONDS` (по умолчанию `10`):

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

Второй сигнал отменяет точку входа сразу, не дожидаясь конца grace period, — например,
двойной Ctrl+C:

```console
$ python -m app.workers.stubborn
queue: connected
stubborn: processing message-1
^C^C
Second SIGINT, cancelling run app.workers.stubborn.stubborn
queue: disconnected
```

Сигнал, пришедший, пока клиенты ещё подключаются, прерывает старт, и уже подключённые
клиенты отключаются.

В худшем случае процесс останавливается за
`SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × длина самой длинной цепочки зависимостей`. Со
значениями по умолчанию цепочка из двух клиентов занимает весь стандартный для Kubernetes `terminationGracePeriodSeconds` в 30 секунд,
поэтому для более глубоких деревьев уменьшите таймауты или увеличьте grace period.

## <a id="background-tasks"></a>Фоновые задачи

`BackgroundTasks` — клиент, который присматривает за корутинами, работающими рядом с точкой входа.
В отличие от голого `asyncio.create_task()`, упавшая задача никогда не теряется: она попадает в лог
с трейсбеком и роняет весь процесс.

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

Воркер был отменён без grace period: упавший фоновый цикл не должен оставлять живой процесс,
который ничего не делает. Когда процесс останавливается по любой причине, задачи отменяются и
дожидаются завершения **до** того, как отключится хоть один клиент, поэтому они никогда не работают
с закрытыми клиентами.

| Метод                      | Описание                                                        |
|----------------------------|-----------------------------------------------------------------|
| `spawn(coro, name=None)`   | Запустить `coro` как задачу и хранить ссылку на неё, пока она не завершится |
| `watch(callback)`          | Вызывать `callback(exc)` для каждой упавшей задачи              |
| `await stop()`             | Отменить все задачи и дождаться каждой; его вызывает `disconnect()` |

Вне воркера или джобы, например под обычным `async with DI`, ошибки только пишутся в лог,
а задачи отменяются в `disconnect()`.

## <a id="exit-codes"></a>Коды завершения

Срабатывает первое подходящее правило:

| Условие                                                                                             | Код завершения |
|-----------------------------------------------------------------------------------------------------|----------------|
| Неверная командная строка (`UsageError`)                                                            | `2`            |
| Исключение: в сигнатуре, при разрешении или подключении клиентов, в точке входа, в фоновой задаче   | `1`            |
| Получен сигнал завершения                                                                           | `128 + signum` |
| Во всех остальных случаях                                                                           | `0`            |

SIGTERM даёт `143`, SIGINT — `130`. Джоба, которая заметила остановку и штатно вернула управление,
всё равно завершается с `128 + signum`: её работа была прервана, и планировщик не должен считать её
выполненной.

Коды предназначены для того, кто запускает процесс:

```bash
python -m app.jobs.sync --day 2026-10-01
case $? in
  0)       echo "synced" ;;
  2)       echo "fix the command line, retrying will not help" ;;
  130|143) echo "interrupted, safe to run again" ;;
  *)       echo "failed, see the log" ;;
esac
```

## <a id="hooks"></a>Хуки

Хуки наблюдают за каждым запуском, например чтобы отправлять метрики или открывать span трассировки:

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

`on_start` вызывается в порядке списка до разрешения клиентов; `on_finish` — в обратном
порядке после их отключения, поэтому он видит итоговое состояние `Run`, включая ошибки
подключения:

| Поле `Run`    | Значение                                                               |
|---------------|------------------------------------------------------------------------|
| `name`        | Модуль и функция, например `app.jobs.report.report`                    |
| `kind`        | `"job"` или `"worker"`                                                 |
| `started_at`  | `datetime` в UTC                                                       |
| `finished_at` | `datetime` в UTC, заполняется до `on_finish`                           |
| `exit_code`   | Код завершения процесса, заполняется до `on_finish`                    |
| `error`       | Исключение, из-за которого упал запуск, например `UsageError`, или `None` |
| `signal`      | Первый полученный сигнал завершения или `None`                         |
| `clients`     | По одному `ClientTiming` на клиент: длительности и исходы подключения и отключения; пусто, если запуск упал до подключения |

Хуки — обычные объекты, а не клиенты: своими ресурсами они управляют сами. Исключение в хуке
попадает в лог и не меняет код завершения. `--help` — не запуск, поэтому хуки его не видят.

### <a id="startup-metrics-and-structured-logs"></a>Метрики старта и структурированные логи

`run.clients` — место для экспорта метрик старта: `on_finish` видит, сколько каждый клиент
подключался и отключался. Кроме того, каждая запись лога `nuke_di` несёт структурированные
поля, так что JSON-форматтер может фильтровать и агрегировать по клиенту, не разбирая
текст сообщений:

```python
# app/jobs/startup.py
import json
import logging

from nuke_di import Run, job

from app.clients import Postgres, Warehouse


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {key: getattr(record, key) for key in ("run", "client", "duration") if hasattr(record, key)}
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
{"level": "INFO", "message": "Connected 4 clients in 0.00s (slowest: Postgres 0.00s, Shutdown 0.00s, Warehouse 0.00s)", "run": "app.jobs.startup.startup", "duration": 0.00022179202642291784}
startup: done
postgres: disconnected
warehouse: disconnected
{"level": "INFO", "message": "Run app.jobs.startup.startup finished with exit code 0 in 0.001s", "run": "app.jobs.startup.startup", "duration": 0.001171}
metric: Shutdown connect=0.000s ok
metric: BackgroundTasks connect=0.000s ok
metric: Postgres connect=0.000s ok
metric: Warehouse connect=0.000s ok
```

| Поле       | Где есть                                                                      |
|------------|-------------------------------------------------------------------------------|
| `run`      | Каждая запись внутри воркера или джобы, включая записи контейнера: имя запуска |
| `client`   | Каждая запись об одном клиенте: разрешение, подключение, отключение, ошибки   |
| `duration` | Секунды: подключённый или отключённый клиент, сводка старта, завершённый запуск |

Каждый запуск подключает и свои клиенты `Shutdown` и `BackgroundTasks`, поэтому они есть
в `run.clients` и в сводке.

## <a id="running-in-kubernetes"></a>Запуск в Kubernetes

Джоба ложится на CronJob, а воркер — на Deployment. Дайте воркеру достаточно
`terminationGracePeriodSeconds`, чтобы уложиться в [бюджет на остановку](#grace-period):

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
      terminationGracePeriodSeconds: 30  # >= SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × longest chain
      containers:
        - name: consumer
          image: registry.example.com/app:1.0
          command: ["python", "-m", "app.workers.consumer"]
          env:
            - {name: SHUTDOWN_GRACE_SECONDS, value: "15"}
```

Разовая догрузка данных за прошлые дни (backfill) — тот же образ с другими параметрами:

```bash
kubectl run sync-backfill --rm -it --restart=Never --image=registry.example.com/app:1.0 \
  --command -- python -m app.jobs.sync --day 2026-09-30 --mode FULL
```
