# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](#development)
[![License](https://img.shields.io/pypi/l/nuke-di)](../../LICENSE)

[English](https://github.com/troyan-dy/nuke-di/blob/master/README.md) · **Русский** · [简体中文](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.zh-CN.md) · [Español](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.es.md) · [Português (Brasil)](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pt-BR.md) · [日本語](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ja.md) · [Polski](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pl.md)

Самое простое внедрение зависимостей для асинхронных проектов на Python.

Зависимости объявляются обычными аннотациями типов. `nuke-di` строит дерево зависимостей,
создаёт каждый клиент один раз и управляет его асинхронным жизненным циклом: `connect()` при старте
и `disconnect()` при остановке. Независимые клиенты стартуют конкурентно, слой за слоем,
начиная с самых глубоких зависимостей.

Вдобавок один декоратор превращает асинхронную функцию в процесс: в **джобу**, которая выполняется
один раз, или в **воркер**, который работает, пока его не остановят, — с параметрами командной строки,
корректным завершением по SIGTERM и осмысленными кодами завершения. Обработчики FastAPI, Litestar
и FastStream получают клиенты по аннотации типа точно так же.

Библиотека выделена из DI-подсистемы продакшен-фреймворка для микросервисов на Python
и не имеет зависимостей во время выполнения.

- [Установка](#installation)
- [Быстрый старт](#quick-start)
- [Клиенты](#clients): [синглтоны](#client-and-notsingletonclient), [жизненный цикл](#connect-and-disconnect), [датаклассы](#dataclass-clients), [слои](#layers), [время старта](#startup-timings), [ошибки подключения](#when-a-client-fails-to-connect), [ошибки разрешения](#when-the-tree-cannot-be-built)
- [Контейнер](#the-container)
- [Воркеры и джобы](#workers-and-jobs): [джоба](#your-first-job), [параметры](#parameters), [воркер](#your-first-worker), [grace period](#grace-period), [фоновые задачи](#background-tasks), [коды завершения](#exit-codes), [хуки](#hooks), [Kubernetes](#running-in-kubernetes)
- Фреймворки: [FastAPI](#fastapi), [Litestar](#litestar), [FastStream](#faststream)
- [Тестирование](#testing)
- [Настройка](#configuration) · [Ошибки](#errors) · [Разработка](#development)

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

## <a id="clients"></a>Клиенты

### <a id="client-and-notsingletonclient"></a>Client и NotSingletonClient

Каждая зависимость — подкласс одного из двух базовых классов:

| Базовый класс        | Экземпляры                                                     |
|----------------------|----------------------------------------------------------------|
| `Client`             | Синглтон: один экземпляр на контейнер                          |
| `NotSingletonClient` | Новый экземпляр для каждого потребителя, который его объявляет |

```python
from nuke_di import Client, Dependencies, NotSingletonClient


class Settings(Client):
    pass


class HttpSession(NotSingletonClient):
    pass


class Orders(Client):
    def __init__(self, settings: Settings, http: HttpSession) -> None:
        self.settings = settings
        self.http = http


class Payments(Client):
    def __init__(self, settings: Settings, http: HttpSession) -> None:
        self.settings = settings
        self.http = http


deps = Dependencies()
orders = deps.resolve(Orders)
payments = deps.resolve(Payments)

print(orders.settings is payments.settings)  # one Settings for the whole container
print(orders.http is payments.http)  # every consumer gets its own HttpSession
print(deps.resolve(Orders) is orders)  # resolve() is idempotent for a Client
```

```text
True
False
True
```

Клиент объявляет свои зависимости как аннотированные аргументы `__init__`. Внедряются только
аргументы, аннотированные типом клиента, и разрешение идёт рекурсивно.

### <a id="connect-and-disconnect"></a>connect() и disconnect()

Переопределите асинхронные методы `connect()` / `disconnect()`, чтобы открывать и освобождать
ресурсы, например пулы соединений. `__init__` только сохраняет зависимости; всё, что выполняет
I/O, место в `connect()`:

```python
class Redis(Client):
    def __init__(self) -> None:
        self._pool: Pool | None = None

    async def connect(self) -> None:
        self._pool = await create_pool()

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
```

Время каждого `connect()` ограничено `CONNECT_TIMEOUT_SECONDS` (по умолчанию `30`), а каждого
`disconnect()` — `DISCONNECT_TIMEOUT_SECONDS` (по умолчанию `10`). Упавший или зависший
`disconnect()` попадает в лог, а остальные клиенты всё равно отключаются.

### <a id="dataclass-clients"></a>Клиенты-датаклассы

`client_dataclass` делает класс одновременно `Client` и датаклассом, так что его поля
становятся внедряемыми зависимостями:

```python
from nuke_di import Client, Dependencies, client_dataclass


class Postgres(Client):
    pass


class Payments(Client):
    pass


@client_dataclass(frozen=True)
class Checkout:
    pg: Postgres
    payments: Payments


checkout = Dependencies().resolve(Checkout)
print(checkout)
print(isinstance(checkout, Client))
```

```text
Checkout(pg=<__main__.Postgres object at 0x...>, payments=<__main__.Payments object at 0x...>)
True
```

Он принимает те же именованные аргументы, что и `dataclasses.dataclass`.

### <a id="layers"></a>Слои

Клиенты подключаются конкурентно, по слоям. Клиенты без зависимостей образуют слой 0;
любой другой клиент находится на один слой выше своей самой высокой зависимости. Слой начинает
подключаться только после того, как подключился предыдущий, поэтому клиент никогда не подключается
раньше собственных зависимостей. `disconnect()` проходит слои в обратном порядке.

```python
import asyncio
import logging

from nuke_di import Client, Dependencies

logging.basicConfig(level=logging.DEBUG, format="%(message)s")
logging.getLogger("asyncio").setLevel(logging.WARNING)  # keep only the nuke_di records


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.2)
        print("  postgres ready")


class Redis(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        print("  redis ready")


class Payments(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Checkout(Client):
    def __init__(self, pg: Postgres, redis: Redis, payments: Payments) -> None:
        self.pg, self.redis, self.payments = pg, redis, payments


async def main() -> None:
    deps = Dependencies()
    deps.resolve(Checkout)
    async with deps:
        print("-- application is running --")


asyncio.run(main())
```

Слои видны в `DEBUG`-логе логгера `nuke_di`:

```text
Resolving dependency "Checkout"
Resolving dependency "Postgres"
Resolving dependency "Redis"
Resolving dependency "Payments"
Connecting layer 0: Postgres, Redis
Connecting client Postgres
Connecting client Redis
  redis ready
Connected client Redis in 0.101s
  postgres ready
Connected client Postgres in 0.201s
Connecting layer 1: Payments
Connecting client Payments
Connected client Payments in 0.000s
Connecting layer 2: Checkout
Connecting client Checkout
Connected client Checkout in 0.000s
Connected 4 clients in 3 layers in 0.20s (slowest: Postgres 0.20s, Redis 0.10s, Payments 0.00s)
-- application is running --
Disconnecting client Checkout
Disconnected client Checkout in 0.000s
Disconnecting client Payments
Disconnected client Payments in 0.000s
Disconnecting client Postgres
Disconnected client Postgres in 0.000s
Disconnecting client Redis
Disconnected client Redis in 0.000s
```

```text
Checkout(pg, redis, payments)    layer 2
Payments(pg)                     layer 1
Postgres, Redis                  layer 0  <- connect together, in 0.2s rather than 0.3s
```

Упорядочиваются только зависимости, объявленные в `__init__`. Если клиенту нужно, чтобы другой
клиент подключился раньше, объявите его зависимостью. Чтобы ограничить число клиентов,
подключающихся одновременно, задайте `CONNECT_CONCURRENCY`.

### <a id="startup-timings"></a>Время старта

Контейнер замеряет `connect()` и `disconnect()` каждого клиента, так что медленный старт
сам называет виновника. После успешного `connect()` он пишет сводку на уровне `INFO` и
`WARNING` для каждого клиента, который потратил больше половины `CONNECT_TIMEOUT_SECONDS`,
задолго до того, как этот клиент начнёт падать по таймауту:

```python
# startup.py
import asyncio
import logging

from nuke_di import Client, Dependencies, DependenciesSettings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.2)


class Kafka(Client):
    async def connect(self) -> None:
        await asyncio.sleep(1.6)

    async def disconnect(self) -> None:
        await asyncio.sleep(0.3)


class Orders(Client):
    def __init__(self, pg: Postgres, kafka: Kafka) -> None:
        self.pg, self.kafka = pg, kafka


async def main() -> None:
    deps = Dependencies(settings=DependenciesSettings(connect_timeout=3))
    deps.resolve(Orders)
    async with deps:
        print("-- application is running --")

    for t in deps.timings:
        print(
            f"{t.name:<8} layer {t.layer}  connect {t.connect:.2f}s {t.connect_outcome:<3}  "
            f"disconnect {t.disconnect:.2f}s {t.disconnect_outcome}"
        )


asyncio.run(main())
```

```console
$ python startup.py
INFO Connected 3 clients in 2 layers in 1.60s (slowest: Kafka 1.60s, Postgres 0.20s, Orders 0.00s)
WARNING Client Kafka took 1.60s to connect, more than half of CONNECT_TIMEOUT_SECONDS (3s)
-- application is running --
Postgres layer 0  connect 0.20s ok   disconnect 0.00s ok
Kafka    layer 0  connect 1.60s ok   disconnect 0.30s ok
Orders   layer 1  connect 0.00s ok   disconnect 0.00s ok
```

`deps.timings` хранит по одному `ClientTiming` на каждый клиент последнего `connect()`, в
порядке подключения. Список переживает `disconnect()`, поэтому его можно прочитать после
остановки контейнера. В приложении FastAPI lifespan, переданный в `FastAPI()`, работает внутри
подключённого контейнера и видит тайминги подключения. Воркер или джоба получают тот же
список в [`Run.clients`](#startup-metrics-and-structured-logs).

| Поле `ClientTiming`  | Значение |
|----------------------|----------|
| `name`               | Имя класса клиента |
| `layer`              | [Слой](#layers) клиента |
| `connect`            | Секунды внутри `connect()` без ожидания `CONNECT_CONCURRENCY`; `None`, если `connect()` не запускался |
| `connect_outcome`    | `"ok"`, `"failed"`, `"timed_out"`, `"cancelled"` или `None`, если `connect()` не начинался |
| `disconnect`, `disconnect_outcome` | То же для `disconnect()`; `None`, пока клиент не отключился |

Когда клиент не может подключиться, клиенты его слоя, которые ещё подключаются, получают
`"cancelled"`, слои выше остаются с `None`, а уже подключённые клиенты откатываются и
получают `disconnect_outcome`. Библиотека только замеряет: экспорт таймингов в метрики
или спаны остаётся за вашим кодом.

### <a id="when-a-client-fails-to-connect"></a>Если клиент не смог подключиться

Если клиент не смог подключиться, остальная часть его слоя отменяется, а следующие слои так и не
стартуют. Уже подключённые клиенты отключаются, слои — в обратном порядке, и контейнер остаётся
отключённым и пустым:

```python
import asyncio

from nuke_di import Client, ConnectError, Dependencies


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")


class Kafka(Client):
    async def connect(self) -> None:
        raise OSError("broker kafka-1:9092 is unreachable")


class Orders(Client):
    def __init__(self, pg: Postgres, kafka: Kafka) -> None:
        self.pg, self.kafka = pg, kafka


async def main() -> None:
    deps = Dependencies()
    deps.resolve(Orders)
    try:
        await deps.connect()
    except ConnectError as exc:
        print(f"{exc} <- {exc.__cause__!r}")
    print("connected:", deps.connected)


asyncio.run(main())
```

```text
postgres: connected
Error occurred connecting client Kafka
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
postgres: disconnected
Error occurred connecting client Kafka <- OSError('broker kafka-1:9092 is unreachable')
connected: False
```

Та же очистка происходит, если отменён сам `connect()`. `ConnectError` наследуется от
`SystemExit`, поэтому приложение, которое его не перехватывает, останавливается — обычно именно это
и нужно, когда зависимость недоступна. Замоканные клиенты не подключаются и не влияют на слои.

### <a id="when-the-tree-cannot-be-built"></a>Если дерево не удаётся построить

Разрешение проверяет каждый `__init__` до его вызова, поэтому клиент, который невозможно построить,
падает ещё до того, как что-либо подключится, — с именем аргумента и путём от запрошенного клиента:

```python
from typing import Protocol

from nuke_di import Client, Dependencies, InvalidSignatureError


class Postgres(Client):
    pass


class UserRepository(Protocol):
    async def get(self, user_id: int) -> str: ...


class Profiles(Client):
    def __init__(self, pg: Postgres, users: UserRepository) -> None:
        self.pg, self.users = pg, users


class Checkout(Client):
    def __init__(self, profiles: Profiles) -> None:
        self.profiles = profiles


class Orders(Client):
    def __init__(self, payments: "Payments") -> None:
        self.payments = payments


class Payments(Client):
    def __init__(self, orders: Orders) -> None:
        self.orders = orders


for root in (Checkout, Orders):
    try:
        Dependencies().resolve(root)
    except InvalidSignatureError as exc:
        print(f"{type(exc).__name__}: {exc}")
```

```text
InvalidSignatureError: Argument "users" of "Profiles.__init__" is UserRepository, which is not a client (resolving Checkout -> Profiles)
CircularDependencyError: Circular dependency: Orders -> Payments -> Orders
```

Аргумент `__init__` заполняется клиентом, если его аннотация типа — клиент. Любому другому
аргументу нужно значение по умолчанию, и его не трогают. С `InvalidSignatureError` падают:

| Аргумент `__init__` без значения по умолчанию | Сообщение                                         |
|-----------------------------------------------|---------------------------------------------------|
| без аннотации типа                            | `has no type hint`                                |
| тип, который не является клиентом             | `is UserRepository, which is not a client`        |
| `Client \| None`                              | `is Postgres \| None, a client cannot be optional` |
| клиент, только позиционный (`/`)              | `is positional-only, a client is passed by keyword` |

Клиенты, циклически зависящие друг от друга, падают с `CircularDependencyError` — подклассом
`InvalidSignatureError`, а аннотация типа, которую не удаётся вычислить (например, класс, определённый
внутри функции или импортированный под `TYPE_CHECKING`), — с `InvalidSignatureError`, где об этом
прямо сказано. Если ошибка пришла из `inject()`, путь начинается с функции:
`(resolving handler -> Checkout -> Profiles)`. В [воркере или джобе](#workers-and-jobs)
любая из этих ошибок завершает запуск с кодом `1` ещё до подключения.

## <a id="the-container"></a>Контейнер

`Dependencies` — это контейнер. `DI` — готовый глобальный экземпляр; создавайте собственный,
когда нужна изоляция, например в тестах.

| Метод                | Описание                                                                |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Построить `cls` и его дерево зависимостей. Идемпотентен для `Client`.   |
| `inject(func)`       | Вернуть `functools.partial(func, ...)` с привязанными аргументами-клиентами. У каждого аргумента `func`, кроме `*args` / `**kwargs`, должна быть аннотация типа. |
| `connect()`          | Вызвать `connect()` у каждого разрешённого клиента, слой за слоем.      |
| `disconnect()`       | Вызвать `disconnect()` слой за слоем в обратном порядке, затем очистить контейнер через `flush()`. |
| `async with`         | `connect()` при входе, `disconnect()` при выходе.                       |
| `mock(cls, new=None)`| Зарегистрировать подмену для `cls` (по умолчанию — autospec-мок) до следующего `flush()`. Вызывается до разрешения `cls`. |
| `override(cls, new=None)` | Подмена на время блока `with`, затем `flush()`; см. [Тестирование](#testing). |
| `flush()`            | Забыть все разрешённые клиенты.                                         |
| `timings`            | По одному `ClientTiming` на клиент последнего `connect()`; см. [Время старта](#startup-timings). |

`resolve`, `inject`, `mock`, `override` и `flush` работают, только пока контейнер отключён:
всё дерево строится до старта.

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: already connected
```

## <a id="workers-and-jobs"></a>Воркеры и джобы

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

### <a id="your-first-job"></a>Первая джоба

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
INFO  nuke_di.core: Connected 4 clients in 1 layer in 0.00s (slowest: Warehouse 0.00s, Postgres 0.00s, Shutdown 0.00s)
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
INFO  nuke_di.run: Run app.jobs.sync.sync finished with exit code 0 in 0.002s
```

#### <a id="one-entrypoint-per-module-defined-last"></a>Одна точка входа на модуль, и она последняя

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

### <a id="parameters"></a>Параметры

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

#### <a id="supported-types"></a>Поддерживаемые типы

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

#### <a id="parameters-in-tests"></a>Параметры в тестах

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

### <a id="your-first-worker"></a>Первый воркер

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

### <a id="grace-period"></a>Grace period

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
`SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers`. Со значениями по умолчанию дерево
из двух слоёв занимает весь стандартный для Kubernetes `terminationGracePeriodSeconds` в 30 секунд,
поэтому для более глубоких деревьев уменьшите таймауты или увеличьте grace period.

### <a id="background-tasks"></a>Фоновые задачи

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

### <a id="exit-codes"></a>Коды завершения

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

### <a id="hooks"></a>Хуки

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

#### <a id="startup-metrics-and-structured-logs"></a>Метрики старта и структурированные логи

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

| Поле       | Где есть                                                                      |
|------------|-------------------------------------------------------------------------------|
| `run`      | Каждая запись внутри воркера или джобы, включая записи контейнера: имя запуска |
| `client`   | Каждая запись об одном клиенте: разрешение, подключение, отключение, ошибки   |
| `layer`    | Каждая запись о подключении или отключении клиента и `Connecting layer`       |
| `duration` | Секунды: подключённый или отключённый клиент, сводка старта, завершённый запуск |

Каждый запуск подключает и свои клиенты `Shutdown` и `BackgroundTasks`, поэтому они есть
в `run.clients` и в сводке.

### <a id="running-in-kubernetes"></a>Запуск в Kubernetes

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
      terminationGracePeriodSeconds: 30  # >= SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers
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

## <a id="fastapi"></a>FastAPI

Операция пути (path operation) в FastAPI получает клиент так же, как джоба, — по аннотации типа.
В обработчиках больше ничего писать не нужно: ни `Depends`, ни `inject()`.

```bash
pip install "nuke-di[fastapi]"
```

Нужен FastAPI 0.105 или новее. Примеры используют общий модуль клиентов:

```python
# app/clients.py
from nuke_di import Client


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
```

API:

```python
# app/api.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di.fastapi import ClientRouter, setup

app = FastAPI()
setup(app)  # before the routes: clients connect on startup, disconnect on shutdown


@app.get("/users/{user_id}")
async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


account = ClientRouter(prefix="/me")


@account.get("")
async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user


app.include_router(account)
```

```console
$ uvicorn app.api:app
INFO:     Started server process [80948]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:54682 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:54684 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [80948]
```

```console
$ curl localhost:8000/users/42
"Hello, user-42!"
$ curl localhost:8000/me -H "X-User-Id: 7"
"user-7"
```

Что произошло:

1. `setup(app)` сделал так, что каждый маршрут, объявленный на `app` после этого вызова, заполняет
   свои аргументы-клиенты из глобального `DI`, и обернул lifespan приложения.
2. `@app.get` увидел `users: UserService` и только запомнил это; при импорте ничего не построено.
3. При старте lifespan разрешил клиенты маршрутов, которые обслуживает приложение, — его собственных
   и маршрутов включённых в него роутеров — и подключил их слой за слоем. При остановке он их отключил.
4. Запрос к `/users/42` получил подключённый `UserService`. `/me` прошёл через зависимость
   `current_user`, которая получает `db: Database` тем же способом.

Правила:

- **Где заполняются клиенты.** В аргументах операций пути, WebSocket-эндпоинтов и всех зависимостей,
  которые они используют, на любой глубине: функций и классов, используемых как `Depends(Auth)` или `Annotated[Auth, Depends()]`,
  включая `dependencies=` маршрута, его роутера, `include_router()` и приложения. Аргумент
  считается клиентом, если его аннотация типа — клиент, в том числе внутри `Annotated[UserService, ...]`
  без `Depends`. Все остальные аргументы достаются FastAPI: path, query, header, body, `Depends`.
- **Роутеры.** Создавайте их через `ClientRouter(...)`, который принимает те же аргументы, что и `APIRouter`,
  и включайте в приложение или в другой `ClientRouter`. `APIRouter(route_class=ClientRoute)`
  подходит для роутера, который не включает другие роутеры. Для другого контейнера используйте
  `setup(app, container)` и `ClientRouter(container=container)`; включение роутера другого
  контейнера сразу выбрасывает `TypeError`.
- **Вызывайте `setup(app)` до маршрутов.** Маршрут с клиентом, объявленный раньше, сразу падает
  с `TypeError`, описанным [ниже](#not-supported).
- **Только то, что обслуживает приложение.** Роутер, который приложение не включает, например
  импортированный только тестом, ничего не подключает при старте приложения.
- **Экземпляры.** Как и с `inject()`, `Client` — это один экземпляр на контейнер, а
  `NotSingletonClient` — один экземпляр на каждый объявляющий его аргумент, а не на каждый запрос.
- **Lifespan.** Собственный `lifespan=` приложения выполняется внутри: его код старта видит подключённые
  клиенты, а код остановки выполняется до их отключения. При остановке взводится `Shutdown` и
  останавливаются `BackgroundTasks`, если приложение их использует, — до отключения клиентов, как в
  воркере. `BackgroundTasks` из самого FastAPI — другой класс и клиентом не является.
- **Функция остаётся функцией.** Теперь её сигнатура показывает FastAPI `Annotated[UserService, Depends(...)]`,
  но прямой вызов с клиентом, например в юнит-тесте, работает как раньше.

**Тестирование.** Импорт приложения ничего не строит, поэтому тест подменяет клиент до того, как
`TestClient` запустит приложение, — через [`override()`](#testing) или фикстуру `global_di`:

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
1 passed in 0.16s
```

`app.dependency_overrides` продолжает работать, в том числе для функции-зависимости, которая принимает клиенты.

**WebSocket.** WebSocket-эндпоинт получает клиенты точно так же — и на `app`, и на `ClientRouter`:

```python
# app/chat.py
from fastapi import FastAPI, WebSocket

from app.clients import UserService
from nuke_di.fastapi import setup

app = FastAPI()
setup(app)


@app.websocket("/greet")
async def greet(websocket: WebSocket, users: UserService) -> None:
    await websocket.accept()
    async for user_id in websocket.iter_text():
        await websocket.send_text(await users.greet(int(user_id)))
```

```python
# tests/test_chat.py
from fastapi.testclient import TestClient

from app.chat import app


def test_greet() -> None:
    with TestClient(app) as client, client.websocket_connect("/greet") as ws:
        ws.send_text("42")
        assert ws.receive_text() == "Hello, user-42!"
```

```console
$ pytest -q tests/test_chat.py
.                                                                        [100%]
1 passed in 0.16s
```

**Клиент, который не смог подключиться**, срывает старт. Lifespan выбрасывает обычный `RuntimeError`,
причиной которого указан `ConnectError`, поскольку `SystemExit` вырвался бы за пределы event loop
сервера, а сервер сообщает об ошибке и завершается:

```python
# app/broken.py
from fastapi import FastAPI

from nuke_di import Client
from nuke_di.fastapi import setup


class Kafka(Client):
    async def connect(self) -> None:
        raise OSError("broker kafka-1:9092 is unreachable")


app = FastAPI()
setup(app)


@app.post("/events")
async def publish(kafka: Kafka) -> None: ...
```

```console
$ uvicorn app.broken:app
INFO:     Started server process [81379]
INFO:     Waiting for application startup.
Error occurred connecting client Kafka
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
ERROR:    Traceback (most recent call last):
  ...
nuke_di.errors.ConnectError: Error occurred connecting client Kafka

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ...
RuntimeError: nuke-di clients failed to start: Error occurred connecting client Kafka

ERROR:    Application startup failed. Exiting.
$ echo $?
3
```

### <a id="not-supported"></a>Что не поддерживается

В этих местах клиенты не принимаются. Каждое из них при объявлении маршрута выбрасывает `TypeError` с объяснением:

| Где                                                     | Что делать вместо этого                       |
|---------------------------------------------------------|-----------------------------------------------|
| Роутер, созданный без `ClientRouter` / `ClientRoute`    | Создать его через `ClientRouter(...)`         |
| WebSocket-эндпоинт на `APIRouter(route_class=ClientRoute)` | Создать роутер через `ClientRouter(...)`    |
| Необязательный клиент, `Database \| None`               | Обычный `Database`                            |
| Связанный метод или вызываемый объект в роли эндпоинта или зависимости | Функция или класс              |

В отличие от этих случаев, маршрут роутера, включённого в обычный `APIRouter` вместо `ClientRouter`,
обнаруживают только старые версии FastAPI. На FastAPI 0.14x он объявляется, и приложение стартует, но
его запросы падают с `RuntimeError: UserService was not started with the app: include the router of its route into
the app or into a ClientRouter, not into a plain APIRouter`.

Запрос, пришедший без lifespan, например через `TestClient(app)` без `with`, получает
`RuntimeError`: `UserService is not connected: start the app with its lifespan`.

## <a id="litestar"></a>Litestar

Обработчик маршрута в Litestar тоже получает клиент по аннотации типа — через плагин:

```bash
pip install "nuke-di[litestar]"
```

Нужен Litestar 2.15 или новее. С клиентами из примеров для [FastAPI](#fastapi):

```python
# app/litestar_api.py
from typing import Annotated

from litestar import Litestar, get
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, HeaderParameter

from app.clients import Database, UserService
from nuke_di.litestar import ClientPlugin


@get("/users/{user_id:int}")
async def get_user(user_id: FromPath[int], users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, HeaderParameter(name="X-User-Id")], db: Database) -> str:
    return await db.fetch_user(x_user_id)


@get("/me", dependencies={"user": Provide(current_user)})
async def me(user: NamedDependency[str]) -> str:
    return user


app = Litestar([get_user, me], plugins=[ClientPlugin()])
```

```console
$ uvicorn app.litestar_api:app
INFO:     Started server process [6801]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51940 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51942 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [6801]
```

```console
$ curl localhost:8000/users/42
Hello, user-42!
$ curl localhost:8000/me -H "X-User-Id: 7"
user-7
```

`ClientPlugin()` нашёл `users: UserService` в `get_user` и `db: Database` в зависимости
`current_user`, передал оба клиента в Litestar как зависимости и подключил их при старте.

Правила:

- **Где заполняются клиенты.** В аргументах HTTP-обработчиков и обработчиков `@websocket`, с которыми
  создаётся приложение, включая обработчики роутеров и контроллеров на любой глубине, а также всех
  зависимостей, объявленных на приложении, роутере, контроллере или обработчике: функций и классов.
- **По имени.** Litestar передаёт зависимости по имени аргумента, поэтому nuke-di регистрирует каждый
  аргумент-клиент под его именем на уровне приложения. Одно имя — один клиент во всём приложении:
  `users: UserService` в одном обработчике и `users: Billing` в другом выбрасывают `TypeError` при создании
  приложения. Зависимость с тем же именем, объявленная приложением, роутером, контроллером или
  обработчиком, имеет приоритет над клиентом.
- **Экземпляры.** `Client` — это один экземпляр на контейнер, а `NotSingletonClient` — один экземпляр
  на имя аргумента.
- **Lifespan.** Клиенты подключаются до собственных `lifespan=` и `on_startup=` приложения и отключаются
  после его хуков `on_shutdown=`, которые Litestar вызывает последними. `Shutdown` и `BackgroundTasks`
  ведут себя так же, как в [FastAPI](#fastapi).
- **Функция остаётся функцией.** Теперь её аргументы-клиенты аннотированы как явные зависимости
  Litestar, значение которых не валидируется, — `Annotated[UserService, Dependency(), SkipValidationMarker()]`:
  именно этого Litestar 2.23 требует вместо зависимости, сопоставленной только по имени. Прямой вызов
  функции работает как раньше.
- **Плагины.** Ставьте `ClientPlugin()` после всех плагинов, которые добавляют обработчики маршрутов:
  он видит те обработчики, которые есть у приложения к моменту, когда до него доходит очередь.
- **Другой контейнер.** `ClientPlugin(container)`.

**Тестирование.** Как и с FastAPI, тест подменяет клиент до того, как `TestClient` запустит приложение:

```python
# tests/test_litestar_api.py
from litestar.testing import TestClient

from app.clients import Database
from app.litestar_api import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_get_user() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/users/1").text == "Hello, alice!"
        assert client.get("/me", headers={"X-User-Id": "7"}).text == "alice"
```

```console
$ pytest -q tests/test_litestar_api.py
.                                                                        [100%]
1 passed in 0.23s
```

**Что не поддерживается.** WebSocket-слушатель, `@websocket_listener` или класс `WebsocketListener`,
клиенты не принимает: Litestar читает его сигнатуру в момент объявления, ещё до того, как её увидит
плагин, поэтому приложение выбрасывает `TypeError` и предлагает вместо него обработчик `@websocket`.
Аргумент-клиент с именем, которое Litestar резервирует за собой, например `state` или `request`, тоже
приводит к `TypeError`. Обработчик, зарегистрированный после создания приложения через `app.register()`,
не виден.

## <a id="faststream"></a>FastStream

Подписчик FastStream получает клиент по аннотации типа рядом с сообщением:

```bash
pip install "nuke-di[faststream]"
```

Нужен FastStream 0.6 или новее, с любым брокером. С клиентами из примеров для [FastAPI](#fastapi):

```python
# app/worker.py
from faststream import FastStream
from faststream.nats import NatsBroker

from app.clients import UserService
from nuke_di.faststream import setup

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
setup(app)  # clients connect before the broker starts, disconnect after it stops


@broker.subscriber("greetings")
async def greet(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))
```

```console
$ faststream run app.worker:app
database: connected
2026-10-08 15:12:52,281 INFO     - FastStream app starting...
2026-10-08 15:12:52,287 INFO     - greetings |            - `Greet` waiting for messages
2026-10-08 15:12:52,287 INFO     - FastStream app started successfully! To exit, press CTRL+C
2026-10-08 15:12:55,078 INFO     - greetings | a747e4d0-2 - Received
Hello, user-42!
2026-10-08 15:12:55,079 INFO     - greetings | a747e4d0-2 - Processed
^C
2026-10-08 15:12:56,222 INFO     - FastStream app shutting down...
2026-10-08 15:12:56,223 INFO     - FastStream app shut down gracefully.
database: disconnected
```

Сообщение было опубликовано так:

```python
# publish.py
import asyncio

from faststream.nats import NatsBroker


async def main() -> None:
    async with NatsBroker("nats://localhost:4222") as broker:
        await broker.publish(42, "greetings")


asyncio.run(main())
```

Правила:

- **Где заполняются клиенты.** В аргументах подписчиков брокеров приложения, в том числе подписчиков
  включённых роутеров, и всех `Depends(...)`, которые они используют, на любой глубине: функций и классов,
  включая `dependencies=` подписчика, его роутера и брокера. Все остальные аргументы достаются
  FastStream: сообщение, его поля, `Context()`.
- **Какие клиенты стартуют.** При старте — клиенты всех подписчиков, которых обслуживают брокеры
  приложения, включая роутеры. Подписчиков можно объявлять как до, так и после `setup(app)`.
- **Lifespan.** Клиенты подключаются до собственных хуков `lifespan=` и `on_startup=` приложения и до
  старта брокеров; отключаются после остановки брокеров и после хуков `after_shutdown=`.
  `Shutdown` и `BackgroundTasks` ведут себя так же, как в [FastAPI](#fastapi). `setup()` работает и с
  `AsgiFastStream`.
- **Экземпляры.** Как и с `inject()`, `Client` — это один экземпляр на контейнер, а
  `NotSingletonClient` — один экземпляр на каждый объявляющий его аргумент, а не на каждое сообщение.
- **Функция остаётся функцией.** Её сигнатура показывает FastStream `Annotated[UserService, Depends(...)]`,
  как в [FastAPI](#fastapi).
- **Одно приложение за раз.** Функция-подписчик и её зависимости переписываются один раз, каким бы ни был
  контейнер, поэтому приложения, которые их разделяют, например по приложению на тест с брокером на
  уровне модуля, запускаются по очереди: приложение, которое стартует, пока работает другое с той же
  функцией, не запускается. Функция-зависимость, которая принимает клиенты, обслуживает обработчики либо
  FastAPI, либо FastStream, но не те и другие сразу.

**Тестирование.** Тестовый брокер FastStream не запускает хуки приложения, поэтому запускайте
приложение внутри него через `TestApp`:

```python
# tests/test_worker.py
import pytest
from faststream import TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.worker import app, broker
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_greet(capsys: pytest.CaptureFixture[str]) -> None:
    with DI.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(1, "greetings")

    assert "Hello, alice!" in capsys.readouterr().out
```

```console
$ pytest -q tests/test_worker.py
.                                                                        [100%]
1 passed in 0.14s
```

Сообщение, обработанное без lifespan приложения, например через `TestNatsBroker(broker)` без `TestApp`,
выбрасывает `RuntimeError: UserService is not connected: start the app with its lifespan`. Подписчик,
добавленный после старта приложения, выбрасывает `RuntimeError: UserService was not started with the app`.

## <a id="testing"></a>Тестирование

**Клиент через контейнер.** Зарегистрируйте моки до разрешения дерева; тогда каждый потребитель
получит мок:

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

**Клиент на один блок, через `override()`.** `override(cls, new=None)` регистрирует подмену,
как `mock()`, но она действует до конца блока `with`, даже на протяжении нескольких циклов `async with`,
а при выходе контейнер очищается, так что ничего из разрешённого с подменой не утекает в следующий тест.
Работает и с глобальным `DI`:

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

Асинхронные тесты в этом разделе используют [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) с
`asyncio_mode = auto` в `pytest.ini`; без него pytest не запускает тесты, объявленные через `async def`.

```console
$ pytest -q -s test_greet.py
Hello, alice!
after the block: OrderedDict()
.Hello, bob!
.
2 passed in 0.01s
```

`database: connected` не выводится ни разу: подмена не подключается.

Правила:

- **Подменяйте до разрешения.** Подмена, зарегистрированная после разрешения `cls`, дошла бы
  только до потребителей, разрешённых позже, а ранние сохранили бы настоящий клиент, поэтому `mock()`
  вместо этого выбрасывает исключение:

  ```python
  DI.inject(handler)  # resolves UserService -> Database
  DI.mock(Database)  # ConnectError: Database is already resolved, call mock() before resolve() or inject()
  ```

- **`override()` начинает с контейнера без разрешённых клиентов.** Иначе очистка при выходе
  молча выбросила бы то, что было разрешено до блока, поэтому он выбрасывает
  `ConnectError: override(Database) needs a container without resolved clients, found: Database, UserService`.
  Сначала вызовите `DI.flush()` или используйте фикстуру `global_di`, описанную ниже.
- **Одна подмена на класс.** Повторный вызов `mock(cls)` возвращает уже зарегистрированную подмену;
  `mock(cls, other)` и `override(cls)` выбрасывают `ConnectError: Database already has a
  replacement`.
- **Подмены не подключаются.** Их `connect()` / `disconnect()` никогда не вызываются, и они
  не участвуют в [слоях](#layers).
- **Сколько живёт подмена.** Подмену из `mock()` сбрасывает следующий `flush()`, включая тот, что
  выполняется в конце `disconnect()`: тесту, который подключает контейнер больше одного раза, стоит
  использовать `override()` — его подмена переживает любой `flush()` до конца своего блока. Исключение
  внутри блока пробрасывается без изменений; обычный выход из блока, пока контейнер ещё подключён,
  выбрасывает `ConnectError`.
- **Вложенность.** Блоки для разных классов можно вкладывать друг в друга, если каждый открывается
  до того, как что-либо разрешено, например `with DI.override(Database), DI.override(Clock):`; выход
  из внутреннего блока сохраняет подмену внешнего.

**Фикстуры pytest.** Установка `nuke-di` регистрирует плагин pytest с двумя фикстурами. Ни одна из
них не autouse, поэтому существующие тесты работают ровно как раньше:

| Фикстура    | Что даёт                                               |
|-------------|--------------------------------------------------------|
| `di`        | Новый `Dependencies` на один тест                      |
| `global_di` | Глобальный `DI`, очищенный до и после теста            |

Тест, который оставил контейнер подключённым, получает ошибку на этапе teardown, а контейнер всё равно
очищается, так что следующий тест начинается с чистого листа:

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

Фикстуры не могут сами отключить забытый контейнер: к моменту teardown event loop теста может быть
уже закрыт. `global_di` защищает только те тесты, которые её запрашивают: тест, использующий глобальный
`DI` без неё, по-прежнему может оставить клиенты следующему. Проект, в котором определена собственная
фикстура `di`, сохраняет её, так как фикстура из `conftest.py` важнее фикстуры плагина;
`pytest -p no:nuke_di` отключает плагин.

**Джоба напрямую.** Импорт модуля не запускает джобу, поэтому вызовите функцию с
моками и параметрами:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    pg.upsert.assert_awaited_once_with("users", ["row"])
```

**Джоба через контейнер**, с клиентами, связанными так же, как в продакшене:

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

**Воркер.** `Shutdown.set()` делает то же, что сделал бы SIGTERM:

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

## <a id="configuration"></a>Настройка

| Переменная окружения         | По умолчанию | Описание                                      |
|------------------------------|--------------|-----------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`         | Таймаут `connect()` одного клиента, в секундах |
| `CONNECT_CONCURRENCY`        | `0`          | Сколько клиентов могут одновременно подключаться или отключаться в пределах контейнера; `0` — без ограничений |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`         | Таймаут `disconnect()` одного клиента, в секундах |
| `SHUTDOWN_GRACE_SECONDS`     | `10`         | Сколько воркер или джоба могут работать после SIGTERM / SIGINT, прежде чем их отменят, в секундах; читается при старте процесса |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

Настройки контейнера читаются при создании экземпляра `Dependencies`. Их можно передать
и явно:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```

## <a id="errors"></a>Ошибки

| Исключение                  | Когда выбрасывается                                       |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | `__init__` клиента выбросил исключение                    |
| `ConnectError`              | `connect()` клиента выбросил исключение или контейнер в неподходящем состоянии (например, разрешение после подключения, мок уже разрешённого клиента, `override()` контейнера с разрешёнными клиентами) |
| `ConnectTimeoutError`       | `connect()` клиента не уложился в `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError`     | В `__init__` клиента есть обязательный аргумент, который не является клиентом, в `inject()` передана функция с аргументом без аннотации типа, или у параметра точки входа неподдерживаемый тип либо конфликтующий флаг; см. [Если дерево не удаётся построить](#when-the-tree-cannot-be-built) |
| `CircularDependencyError`   | Клиенты циклически зависят друг от друга; подкласс `InvalidSignatureError` |
| `UsageError`                | Командная строка воркера или джобы не соответствует её параметрам; записывается в `Run.error`, код завершения `2` |

`InitializeDependencyError` и `ConnectError` наследуются от `SystemExit`: предполагается, что
приложение, чьи зависимости не могут стартовать, должно остановиться. Если нужно другое поведение,
перехватывайте их явно; исходное исключение доступно в `__cause__`.

`nuke-di` пишет логи через стандартный модуль `logging` в логгер `nuke_di`, со
[структурированными полями](#startup-metrics-and-structured-logs) для лог-пайплайнов.

## <a id="development"></a>Разработка

```bash
make install   # uv sync --locked
make check     # ruff, mypy and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

Покрытие строк и ветвлений — 100%, и CI падает, если оно опускается ниже
(`fail_under = 100` в `pyproject.toml`).

### <a id="releases"></a>Релизы

Каждый мерж в `master` — это релиз. Workflow `Release` публикует версию из
`pyproject.toml` в PyPI, ставит тег `vX.Y.Z` и создаёт релиз на GitHub из её раздела в
`CHANGELOG.md`. Поэтому pull request несёт собственную версию: поднимите её командой `uv version --bump
patch|minor|major` и превратите `## [Unreleased]` в `## [X.Y.Z] - YYYY-MM-DD` со ссылкой на сравнение
внизу файла. CI проверяет это в каждом pull request, а локально — `make check-version`:

```console
$ make check-version
git fetch --quiet --tags origin master
uv run --no-project python scripts/version.py check origin/master
error: version 1.5.0 is not above 1.5.0 on master: bump it, e.g. `uv version --bump minor`
error: v1.5.0 is released already
make: *** [check-version] Error 1

$ uv version --bump patch
...
nuke-di 1.5.0 => 1.5.1
$ make check-version
git fetch --quiet --tags origin master
uv run --no-project python scripts/version.py check origin/master
1.5.1
```

Изменение, попавшее в `master` без новой версии, например запушенное напрямую, роняет workflow
`Release` ещё до сборки и публикации.

## <a id="license"></a>Лицензия

[MIT](../../LICENSE)
