# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](ru/development.md)
[![License](https://img.shields.io/pypi/l/nuke-di)](../../LICENSE)

[English](https://github.com/troyan-dy/nuke-di/blob/master/README.md) · **Русский** · [简体中文](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.zh-CN.md) · [Español](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.es.md) · [Português (Brasil)](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pt-BR.md) · [日本語](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ja.md) · [Polski](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pl.md)

Самое простое внедрение зависимостей для асинхронных проектов на Python.

Зависимости объявляются обычными аннотациями типов. `nuke-di` строит дерево зависимостей,
создаёт каждый клиент один раз и управляет его асинхронным жизненным циклом: `connect()` при старте
и `disconnect()` при остановке. Каждый клиент стартует, как только подключились его собственные
зависимости, конкурентно со всеми остальными готовыми клиентами.

Вдобавок один декоратор превращает асинхронную функцию в процесс с параметрами командной строки,
а обработчики FastAPI, Litestar и FastStream получают клиенты по аннотации типа точно так же.

Библиотека выделена из DI-подсистемы продакшен-фреймворка для микросервисов на Python
и не имеет зависимостей во время выполнения.

- [Установка](#installation) · [Быстрый старт](#quick-start) · [Принципы](#principles) · [Производительность](#performance)
- Примеры: [джоба с параметрами командной строки](#a-job-with-command-line-arguments) · [FastAPI](#fastapi)
- [Документация](#documentation)

## <a id="installation"></a>Установка

```bash
pip install nuke-di
```

Нужен Python 3.11+.

## <a id="quick-start"></a>Быстрый старт

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

Что произошло:

1. `DI.inject(handler)` прочитал аннотации типов `handler`, нашёл клиент `UserService`, увидел,
   что тому в `__init__` нужен `Database`, и построил оба. `user_id: int` — не клиент,
   поэтому он остаётся обычным аргументом.
2. `async with DI` вызвал `connect()` у каждого построенного клиента, начиная с зависимостей.
3. `injected(42)` вызвал `handler(42, users=<UserService>)`.
4. При выходе из блока `async with` был вызван `disconnect()` в обратном порядке.

## <a id="principles"></a>Принципы

- **Зависимость — это класс.** Подкласс `Client` с аннотированным `__init__` и асинхронными
  `connect()` / `disconnect()` — это вся модель: ни провайдеров, ни модулей, ни регистрации, ни
  скоупов, которые нужно настраивать. Сторонний объект становится зависимостью, если обернуть его в такой класс.
- **Аннотации типов и есть связывание.** Клиент запрашивает свои зависимости в `__init__`, функция —
  в своей сигнатуре. Больше нигде они не названы, поэтому переименование или добавление зависимости —
  обычный рефакторинг.
- **Конкурентный старт, упорядоченная остановка.** Клиент подключается, как только подключились его
  собственные зависимости, конкурентно со всеми остальными готовыми клиентами, поэтому медленный клиент
  задерживает только тех, кому он нужен. Отключаются они в обратном порядке, и упавший `disconnect()` не
  мешает остальным.
- **Падать сразу.** Дерево, которое не удаётся построить, падает до того, как что-либо подключится, — с
  именем аргумента и путём к нему, а `mypy` с [плагином](ru/clients.md#checking-the-tree-with-mypy)
  сообщает ту же ошибку ещё до запуска процесса. Клиент, который не смог подключиться, останавливает
  приложение, как только уже подключённые клиенты отключены. Повторных попыток нет: перезапуск —
  забота оркестратора.
- **Тесты подменяют, а не перестраивают связи.** `mock()` и `override()` ставят подделку на место
  клиента на время одного теста; тестируемый код не меняется.
- **Никаких зависимостей во время выполнения.** Ядро использует только стандартную библиотеку;
  интеграции с фреймворками ставятся как extras.

Как проходит старт, на примере из [гайда по клиентам](ru/clients.md#connect-order): `Consumer` нужен только
`Kafka`, поэтому он не ждёт медленный `Postgres`, и старт длится столько, сколько самая длинная цепочка
зависимостей.

![Шесть клиентов подключаются по собственным зависимостям: Consumer и Http стартуют, как только подключились Kafka и Redis, старт занимает 0.35s](https://raw.githubusercontent.com/troyan-dy/nuke-di/66f74f76407da320cfc97ef22b761d85e298eddd/docs/connect-now.svg)

## <a id="performance"></a>Производительность

`benchmarks/compare.py` прогоняет одни и те же деревья клиентов через dishka, wireup, dependency-injector
и injector, регистрируя одни и те же классы так, как принято в каждой библиотеке: холодный контейнер с
разрешённым корнем на классах, новых для процесса, повторное получение корня и один запрос FastAPI через
интеграцию каждой библиотеки:

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

Так быстрее ли `nuke-di` всех? При построении дерева с настоящими аннотациями типов и на запросе
FastAPI — да: dependency-injector и injector строят дерево в 2–2,5 раза дольше, dishka и wireup — в
24–37 раз дольше из-за валидации графа при создании контейнера, а wireup и dependency-injector тратят
вдвое больше на каждый запрос. Со строковыми аннотациями dependency-injector, который аннотаций не читает,
опережает `nuke-di` на пятую часть. На закэшированном корне `nuke-di` идёт вровень с wireup, а `get()`
dependency-injector на Cython выигрывает примерно 50 ns: разницу, которую ни одно приложение не заметит.

Сам по себе `resolve()` стоит 3,5–6,3 µs на клиента, так что дерево из 1000 клиентов строится меньше
чем за 5,5 ms, а `connect()` добавляет 13–18 µs на клиента. [docs/benchmarks.md](../benchmarks.md)
описывает каждый сценарий, хранит базовые замеры на Python 3.11–3.14 и содержит полное сравнение
с методикой.

## <a id="a-job-with-command-line-arguments"></a>Джоба с параметрами командной строки

Один декоратор превращает асинхронную функцию в основную программу процесса. Клиенты внедряются,
а каждый другой аннотированный аргумент становится опцией командной строки — типизированной
и проверяемой:

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

Ни `main()`, ни `asyncio.run()`, ни `argparse`: декоратор разрешает и подключает клиенты, разбирает
командную строку, выполняет функцию и завершает процесс с осмысленным кодом:

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

`--help` генерируется из сигнатуры и docstring (Python 3.13+ выводит `-d, --day DAY` вместо
`-d DAY, --day DAY`):

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

Неверная командная строка отклоняется до того, как подключится хоть один клиент, с кодом
завершения `2`:

```console
$ python sync.py -d 2026-10-01 --mode full
usage: sync.py [-h] -d DAY [-t TABLES] [--mode {INCREMENTAL,FULL}]
               [--dry-run | --no-dry-run]
sync.py: error: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
Run sync.sync failed: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
$ echo $?
2
```

`@worker` делает то же самое для процесса, который работает до SIGTERM, с корректным завершением.
Оба описаны в разделе [Воркеры и джобы](ru/workers-and-jobs.md).

## <a id="fastapi"></a>FastAPI

Операция пути (path operation) получает клиент по аннотации типа, без `Depends` и без `inject()`
в каждом обработчике. В `app/clients.py` лежат классы `Database` и `UserService` из
[Быстрого старта](#quick-start), без его `main()`:

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

Клиенты подключаются при старте и отключаются при остановке, а зависимость вроде `current_user`
получает клиенты тем же способом. Импорт приложения ничего не строит, поэтому тест подменяет клиент
до того, как `TestClient` запустит приложение:

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

Роутеры, WebSocket и собственный lifespan приложения описаны в разделе [FastAPI](ru/fastapi.md);
[Litestar](ru/litestar.md) и [FastStream](ru/faststream.md) работают так же.

## <a id="documentation"></a>Документация

- [Клиенты](ru/clients.md): `Client` и `NotSingletonClient`, жизненный цикл, клиенты-датаклассы,
  порядок подключения, время старта, граф зависимостей, ошибки подключения и разрешения
- [Контейнер](ru/container.md): `Dependencies` и глобальный `DI`, `resolve()`, `inject()`,
  `mock()`, `override()`
- [Воркеры и джобы](ru/workers-and-jobs.md): `@job` и `@worker`, параметры командной строки,
  `Shutdown`, grace period, фоновые задачи, коды завершения, хуки, Kubernetes
- Фреймворки: [FastAPI](ru/fastapi.md), [Litestar](ru/litestar.md),
  [FastStream](ru/faststream.md), а также
  [своя интеграция](ru/integrations.md) для другого фреймворка на `nuke_di.integration`
- [Тестирование](ru/testing.md): `mock()`, `override()`, фикстуры pytest, проверка связывания
- [Настройка](ru/configuration.md): таймауты, конкурентность и grace period
- [Ошибки](ru/errors.md): все исключения и когда они выбрасываются
- [Примеры](../../examples/README.md): 21 готовый к запуску сценарий, от разового скрипта и воркера очереди
  до FastAPI, Litestar, FastStream, Starlette и целого сервиса, каждый с выводом и тестами
- [Бенчмарки](../benchmarks.md): каждый сценарий, базовые замеры на Python 3.11–3.14 и сравнение
  с другими библиотеками
- [Агенты для написания кода](ru/agents.md): Agent Skill, блок для `AGENTS.md`,
  [`llms.txt`](https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms.txt), Context7, граф в JSON
- [Разработка](ru/development.md): проверки, покрытие и релизы

## <a id="license"></a>Лицензия

[MIT](../../LICENSE)
