# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](#development)
[![License](https://img.shields.io/pypi/l/nuke-di)](../../LICENSE)

[English](https://github.com/troyan-dy/nuke-di/blob/master/README.md) · [Русский](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ru.md) · [简体中文](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.zh-CN.md) · **Español** · [Português (Brasil)](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pt-BR.md) · [日本語](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ja.md) · [Polski](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pl.md)

La inyección de dependencias más sencilla para proyectos asíncronos en Python.

Las dependencias se declaran con type hints normales y corrientes. `nuke-di` construye el árbol
de dependencias, crea cada cliente una sola vez y gestiona su ciclo de vida asíncrono: `connect()`
al arrancar y `disconnect()` al apagarse. Los clientes independientes arrancan de forma concurrente,
capa por capa, desde las dependencias más profundas hacia arriba.

Además, un solo decorador convierte una función asíncrona en un proceso: un **job** que se ejecuta
una vez o un **worker** que se ejecuta hasta que se le pide detenerse, con parámetros de línea de
comandos, apagado ordenado ante SIGTERM y códigos de salida con significado. Los handlers de FastAPI,
Litestar y FastStream reciben clientes por su type hint de la misma manera.

Nació a partir de la capa de DI de un framework de microservicios en Python usado en producción
y no tiene dependencias en tiempo de ejecución.

- [Instalación](#installation)
- [Inicio rápido](#quick-start)
- [Clientes](#clients): [singletons](#client-and-notsingletonclient), [ciclo de vida](#connect-and-disconnect), [dataclasses](#dataclass-clients), [capas](#layers), [tiempos de arranque](#startup-timings), [fallos de conexión](#when-a-client-fails-to-connect), [errores de resolución](#when-the-tree-cannot-be-built)
- [El contenedor](#the-container)
- [Workers y jobs](#workers-and-jobs): [un job](#your-first-job), [parámetros](#parameters), [un worker](#your-first-worker), [periodo de gracia](#grace-period), [tareas en segundo plano](#background-tasks), [códigos de salida](#exit-codes), [hooks](#hooks), [Kubernetes](#running-in-kubernetes)
- Frameworks: [FastAPI](#fastapi), [Litestar](#litestar), [FastStream](#faststream)
- [Pruebas](#testing)
- [Configuración](#configuration) · [Errores](#errors) · [Desarrollo](#development)

## <a id="installation"></a>Instalación

```bash
pip install nuke-di
```

Requiere Python 3.11 o superior.

## <a id="quick-start"></a>Inicio rápido

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

Qué pasó:

1. `DI.inject(handler)` leyó los type hints de `handler`, encontró el cliente `UserService`, vio
   que necesita un `Database` en su `__init__` y construyó ambos. `user_id: int` no es un
   cliente, así que sigue siendo un argumento normal.
2. `async with DI` llamó a `connect()` en cada cliente que había construido, empezando por las dependencias.
3. `injected(42)` llamó a `handler(42, users=<UserService>)`.
4. Al salir del bloque `async with` se llamó a `disconnect()` en orden inverso.

## <a id="clients"></a>Clientes

### <a id="client-and-notsingletonclient"></a>Client y NotSingletonClient

Toda dependencia es una subclase de una de estas dos clases base:

| Clase base           | Instancias                                             |
|----------------------|--------------------------------------------------------|
| `Client`             | Singleton: una instancia por contenedor                |
| `NotSingletonClient` | Una instancia nueva para cada consumidor que la declara |

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

Un cliente declara sus propias dependencias como argumentos anotados de `__init__`. Solo se
inyectan los argumentos anotados con un tipo de cliente, y la resolución es recursiva.

### <a id="connect-and-disconnect"></a>connect() y disconnect()

Sobrescribe los métodos asíncronos `connect()` / `disconnect()` para abrir y liberar recursos
como los pools de conexiones. `__init__` solo guarda las dependencias; todo lo que haga E/S va
en `connect()`:

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

Cada `connect()` está limitado por `CONNECT_TIMEOUT_SECONDS` (por defecto `30`) y cada
`disconnect()` por `DISCONNECT_TIMEOUT_SECONDS` (por defecto `10`). Un `disconnect()` que falla
o se cuelga queda registrado en el log, y el resto de los clientes se apaga igualmente.

### <a id="dataclass-clients"></a>Clientes como dataclass

`client_dataclass` convierte una clase en `Client` y en dataclass a la vez, de modo que sus campos
pasan a ser las dependencias inyectadas:

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

Acepta los mismos argumentos con nombre que `dataclasses.dataclass`.

### <a id="layers"></a>Capas

Los clientes se conectan de forma concurrente por capas. Los clientes sin dependencias forman la
capa 0; cualquier otro cliente se ubica una capa por encima de su dependencia más alta. Una capa
solo arranca cuando la anterior ya se conectó, así que un cliente nunca se conecta antes que sus
propias dependencias. `disconnect()` recorre las capas en orden inverso.

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

El log `DEBUG` del logger `nuke_di` muestra las capas:

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

Solo se ordenan las dependencias declaradas en `__init__`. Si un cliente necesita que otro esté
conectado antes, decláralo como dependencia. Configura `CONNECT_CONCURRENCY` para limitar cuántos
clientes se conectan a la vez.

### <a id="startup-timings"></a>Tiempos de arranque

El contenedor mide el `connect()` y el `disconnect()` de cada cliente, así que un arranque
lento señala al culpable. Tras un `connect()` exitoso registra un resumen en `INFO` y un
`WARNING` por cada cliente que usó más de la mitad de `CONNECT_TIMEOUT_SECONDS`, mucho
antes de que ese cliente empiece a fallar por timeout:

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

`deps.timings` guarda un `ClientTiming` por cada cliente del último `connect()`, en orden de
conexión. Sobrevive a `disconnect()`, así que se puede leer cuando el contenedor ya se ha
detenido. En una app de FastAPI, el lifespan que pasas a `FastAPI()` se ejecuta dentro del
contenedor conectado, así que ve los tiempos de conexión. Un worker o un job recibe la misma
lista en [`Run.clients`](#startup-metrics-and-structured-logs).

| Campo de `ClientTiming` | Valor |
|-------------------------|-------|
| `name`               | El nombre de la clase del cliente |
| `layer`              | La [capa](#layers) del cliente |
| `connect`            | Segundos dentro de `connect()`, sin contar la espera por `CONNECT_CONCURRENCY`; `None` si `connect()` nunca se ejecutó |
| `connect_outcome`    | `"ok"`, `"failed"`, `"timed_out"`, `"cancelled"`, o `None` si `connect()` nunca empezó |
| `disconnect`, `disconnect_outcome` | Lo mismo para `disconnect()`; `None` hasta que el cliente se desconecta |

Cuando un cliente no logra conectarse, los clientes de su capa que aún se están conectando
quedan en `"cancelled"`, las capas superiores mantienen `None` y los clientes que ya se
habían conectado se revierten, por lo que reciben un `disconnect_outcome`. La biblioteca
solo mide: exportar los tiempos como métricas o spans queda en manos de tu código.

### <a id="when-a-client-fails-to-connect"></a>Cuando un cliente no logra conectarse

Si un cliente no logra conectarse, se cancela el resto de su capa y las capas siguientes nunca
arrancan. Los clientes que ya se habían conectado se desconectan, capa por capa en orden inverso,
y el contenedor queda desconectado y vacío:

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

La misma limpieza ocurre cuando se cancela el propio `connect()`. `ConnectError` deriva de
`SystemExit`, así que una aplicación que no la captura se detiene, que es justo lo que se suele
querer cuando una dependencia está caída. Los clientes mockeados no se conectan y no afectan a las capas.

### <a id="when-the-tree-cannot-be-built"></a>Cuando el árbol no se puede construir

La resolución revisa cada `__init__` antes de llamarlo, así que un cliente que no se puede
construir falla antes de que se conecte nada, indicando el argumento y la ruta desde el cliente
que pediste:

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

Un argumento de `__init__` recibe un cliente cuando su type hint es un cliente. Cualquier otro
argumento necesita un valor por defecto, que se deja tal cual. Estos casos fallan con `InvalidSignatureError`:

| Argumento de `__init__` sin valor por defecto | Mensaje                                    |
|-----------------------------------------------|--------------------------------------------|
| sin type hint                                 | `has no type hint`                         |
| un tipo que no es un cliente                  | `is UserRepository, which is not a client` |
| `Client \| None`                              | `is Postgres \| None, a client cannot be optional` |
| un cliente, solo posicional (`/`)             | `is positional-only, a client is passed by keyword` |

Los clientes que dependen unos de otros en un ciclo fallan con `CircularDependencyError`, una
subclase de `InvalidSignatureError`, y un type hint que no se puede evaluar, por ejemplo una clase
definida dentro de una función o importada bajo `TYPE_CHECKING`, falla con un `InvalidSignatureError`
que lo explica. Cuando el error viene de `inject()`, la ruta empieza en la función:
`(resolving handler -> Checkout -> Profiles)`. En un [worker o un job](#workers-and-jobs)
cada uno de estos errores hace fallar la ejecución con el código de salida `1` antes de que se conecte nada.

## <a id="the-container"></a>El contenedor

`Dependencies` es el contenedor. `DI` es una instancia global lista para usar; crea la tuya
cuando necesites aislamiento, por ejemplo en las pruebas.

| Método               | Descripción                                                             |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Construye `cls` y su árbol de dependencias. Idempotente para `Client`.  |
| `inject(func)`       | Devuelve `functools.partial(func, ...)` con los argumentos de tipo cliente ya vinculados. Todo argumento de `func`, salvo `*args` / `**kwargs`, debe tener type hint. |
| `connect()`          | Llama a `connect()` en cada cliente resuelto, capa por capa.            |
| `disconnect()`       | Llama a `disconnect()` capa por capa en orden inverso y luego hace `flush()` del contenedor. |
| `async with`         | `connect()` al entrar, `disconnect()` al salir.                         |
| `mock(cls, new=None)`| Registra un Reemplazo para `cls` (por defecto, un mock con autospec) hasta el siguiente `flush()`. Debe llamarse antes de resolver `cls`. |
| `override(cls, new=None)` | Un Reemplazo que dura lo que dura un bloque `with`, seguido de `flush()`; consulta [Pruebas](#testing). |
| `flush()`            | Olvida todos los clientes resueltos.                                    |
| `timings`            | Un `ClientTiming` por cliente del último `connect()`; ver [Tiempos de arranque](#startup-timings). |

`resolve`, `inject`, `mock`, `override` y `flush` solo funcionan mientras el contenedor está
desconectado: todo el árbol se construye antes del arranque.

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: already connected
```

## <a id="workers-and-jobs"></a>Workers y jobs

Una función asíncrona se convierte en el programa principal de un proceso con un solo decorador:

| Decorador | Se ejecuta                                                  |
|-----------|-------------------------------------------------------------|
| `@job`    | Una vez: el proceso termina cuando la función retorna       |
| `@worker` | Hasta que el proceso recibe SIGTERM o SIGINT                |

Los ejemplos de esta sección comparten un mismo módulo de clientes:

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

### <a id="your-first-job"></a>Tu primer job

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

Ese es el programa completo: sin `main()`, sin `asyncio.run()`, sin `if __name__ == "__main__"`.
El proceso resuelve los clientes desde el contenedor global `DI`, los conecta, ejecuta la
función, los desconecta y termina con un [código de salida](#exit-codes). La planificación no forma
parte de la librería: un CronJob de Kubernetes, un timer de systemd o crontab deciden cuándo se ejecuta un job.

`nuke-di` registra cada ejecución en el logger `nuke_di`. Configura el logging antes del decorador
para verlo:

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

#### <a id="one-entrypoint-per-module-defined-last"></a>Un punto de entrada por módulo, definido al final

Cuando el módulo se ejecuta como `__main__`, el decorador ejecuta la función en ese mismo momento y el
proceso termina ahí:

```python
# app/jobs/sync.py
DI.mock(Warehouse, FakeWarehouse())  # runs: code above the decorator is fine


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None: ...


print("never printed")  # never runs under `python -m app.jobs.sync`
```

**Mantén un solo punto de entrada por módulo y defínelo al final.** En una importación normal, por
ejemplo desde una prueba, el decorador devuelve la función sin cambios y no se ejecuta nada. La
función decorada debe declararse con `async def`; de lo contrario, se lanza `TypeError` al importarla.

### <a id="parameters"></a>Parámetros

Todo argumento anotado que no sea un cliente se convierte en una opción de línea de comandos. Este es
el mismo job, ahora capaz de copiar cualquier día, un subconjunto de tablas y en modo simulación:

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

`pg` y `warehouse` son clientes y se inyectan; `day`, `tables`, `mode` y `dry_run` vienen
de la línea de comandos:

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

`--help` se genera a partir de la firma y del docstring. No conecta nada
(Python 3.13+ muestra `-d, --day DAY` en lugar de `-d DAY, --day DAY`):

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

Una línea de comandos incorrecta se rechaza **antes de resolver o conectar ningún cliente**, con el
código de salida `2`:

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

Las dos primeras líneas de cada error las imprime `argparse`; la línea `Run ... failed` es el
registro `ERROR` del logger `nuke_di`, así que respeta tu configuración de logging.
No se aceptan abreviaturas: `--dry` no se interpreta como `--dry-run`.

#### <a id="supported-types"></a>Tipos admitidos

| Anotación                                     | Línea de comandos                   | Ejemplo                         |
|-----------------------------------------------|-------------------------------------|---------------------------------|
| `str`, `int`, `float`, `pathlib.Path`         | `--name VALUE`                      | `--limit 10`                    |
| `bool`                                        | `--name` / `--no-name`              | `--dry-run`                     |
| `datetime.date`, `datetime.datetime`          | ISO 8601                            | `--since 2026-10-01T12:00:00`   |
| un `Enum`                                     | el **nombre** del miembro, tal cual | `--mode FULL`                   |
| `list[T]` de cualquiera de los anteriores salvo `bool` | la opción repetida         | `--table users --table orders`  |
| `T \| None`                                   | como `T`                            | `--limit 10`                    |

Las reglas:

- **El nombre.** La opción toma el nombre del argumento, con `_` sustituido por `-`:
  `dry_run` pasa a ser `--dry-run`. No hay argumentos posicionales, así que agregar un parámetro
  nunca rompe una línea de comandos existente.
- **Obligatorio o no.** Un argumento sin valor por defecto es una opción obligatoria. Un argumento
  con valor por defecto es opcional y, cuando se omite la opción, se usa el valor por defecto de la propia función.
- **`Option`.** `Annotated[T, Option(help=..., short=...)]` agrega un texto de ayuda y un alias de
  una letra como `-d`. Ambos son opcionales.
- **Sin parámetros.** Un punto de entrada sin parámetros igualmente analiza su línea de comandos:
  responde a `--help` y rechaza cualquier argumento con el código de salida `2`.

Estas firmas son errores del código, no de la línea de comandos. Hacen fallar la ejecución con
`InvalidSignatureError` y el código de salida `1`:

```python
async def sync(day: dict[str, int]) -> None: ...  # unsupported type
async def sync(pg: Annotated[Postgres, Option(help="...")]) -> None: ...  # Option on a client
async def sync(help: bool = False) -> None: ...  # clashes with --help
async def sync(day: int, /) -> None: ...  # positional-only
```

#### <a id="parameters-in-tests"></a>Parámetros en las pruebas

La función decorada sigue siendo una corrutina común y corriente, así que una prueba le pasa los
parámetros como argumentos con nombre:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    warehouse.changes.assert_awaited_once_with("users", datetime.date(2026, 10, 1))
    pg.upsert.assert_awaited_once_with("users", ["row"])
```

### <a id="your-first-worker"></a>Tu primer worker

Un worker se ejecuta hasta que se le pide al proceso que se detenga. Depende del cliente `Shutdown`,
que se activa con el primer SIGTERM o SIGINT, y termina la unidad de trabajo en curso:

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

Ctrl+C en medio del tercer mensaje: el mensaje se termina de procesar, el bucle finaliza y los
clientes se desconectan.

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

`Shutdown` tiene tres métodos:

| Método           | Descripción                                                                   |
|------------------|-------------------------------------------------------------------------------|
| `is_set()`       | Indica si ya comenzó un Shutdown; compruébalo entre una unidad de trabajo y otra |
| `await wait()`   | Bloquea hasta que comienza un Shutdown                                        |
| `set()`          | Inicia un Shutdown a mano, por ejemplo en una prueba                          |

Fuera de un worker o de un job nadie lo activa, así que un bucle que depende de `Shutdown` también
funciona sin cambios dentro de una aplicación web. Un worker que retorna o lanza una excepción por sí
solo también termina el proceso: reiniciarlo es tarea del orquestador.

En Windows solo se maneja SIGINT (Ctrl+C); SIGTERM conserva su comportamiento por defecto.

### <a id="grace-period"></a>Periodo de gracia

Un worker que ignora `Shutdown` se cancela pasados `SHUTDOWN_GRACE_SECONDS` (por defecto `10`):

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

Una segunda señal cancela el punto de entrada de inmediato, sin esperar a que termine el periodo de
gracia, por ejemplo Ctrl+C dos veces:

```console
$ python -m app.workers.stubborn
queue: connected
stubborn: processing message-1
^C^C
Second SIGINT, cancelling run app.workers.stubborn.stubborn
queue: disconnected
```

Una señal que llega mientras los clientes todavía se están conectando detiene el arranque, y los
clientes que ya se habían conectado se desconectan.

En el peor caso, un proceso se detiene en
`SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers`. Con los valores por defecto, un árbol
de dos capas consume todo el `terminationGracePeriodSeconds` por defecto de Kubernetes, de 30 segundos,
así que para árboles más profundos reduce los timeouts o aumenta el periodo de gracia.

### <a id="background-tasks"></a>Tareas en segundo plano

`BackgroundTasks` es un cliente que supervisa corrutinas que se ejecutan junto al punto de entrada.
A diferencia de un `asyncio.create_task()` a secas, una tarea que falla nunca se pierde: se registra
en el log con su traceback y hace fallar todo el proceso.

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

El worker se canceló sin periodo de gracia: un bucle en segundo plano que se cayó no debe dejar
vivo un proceso que no hace nada. Cuando el proceso se detiene por cualquier motivo, las tareas se
cancelan y se esperan **antes** de que se desconecte cualquier cliente, así que nunca se ejecutan
contra clientes cerrados.

| Método                     | Descripción                                                     |
|----------------------------|-----------------------------------------------------------------|
| `spawn(coro, name=None)`   | Inicia `coro` como tarea y conserva una referencia a ella hasta que termine |
| `watch(callback)`          | Llama a `callback(exc)` por cada tarea que falla                |
| `await stop()`             | Cancela todas las tareas y espera a que terminen; `disconnect()` lo llama |

Fuera de un worker o de un job, por ejemplo con un simple `async with DI`, los fallos solo se
registran en el log y las tareas se cancelan en `disconnect()`.

### <a id="exit-codes"></a>Códigos de salida

Gana la primera regla que se cumple:

| Condición                                                                                                 | Código de salida |
|-----------------------------------------------------------------------------------------------------------|------------------|
| Una línea de comandos no válida (`UsageError`)                                                            | `2`              |
| Una excepción: la firma, la resolución o la conexión de los clientes, el punto de entrada, una tarea en segundo plano | `1`   |
| Se recibió una señal de terminación                                                                       | `128 + signum`   |
| En cualquier otro caso                                                                                    | `0`              |

SIGTERM da `143` y SIGINT da `130`. Un job que detecta un Shutdown y retorna limpiamente
igualmente termina con `128 + signum`: su trabajo se interrumpió, y un planificador no debe darlo
por completado.

Los códigos están pensados para quien sea que lance el proceso:

```bash
python -m app.jobs.sync --day 2026-10-01
case $? in
  0)       echo "synced" ;;
  2)       echo "fix the command line, retrying will not help" ;;
  130|143) echo "interrupted, safe to run again" ;;
  *)       echo "failed, see the log" ;;
esac
```

### <a id="hooks"></a>Hooks

Los hooks observan cada ejecución, por ejemplo para enviar métricas o abrir un span de tracing:

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

`on_start` se llama en el orden de la lista antes de resolver los clientes; `on_finish`, en orden
inverso después de que se hayan desconectado, así que ve el estado final del `Run`, incluidos los
fallos de conexión:

| Campo de `Run` | Valor                                                                  |
|----------------|------------------------------------------------------------------------|
| `name`         | Módulo y función, por ejemplo `app.jobs.report.report`                 |
| `kind`         | `"job"` o `"worker"`                                                   |
| `started_at`   | `datetime` en UTC                                                      |
| `finished_at`  | `datetime` en UTC, se asigna antes de `on_finish`                      |
| `exit_code`    | El código de salida del proceso, se asigna antes de `on_finish`        |
| `error`        | La excepción que hizo fallar la ejecución, por ejemplo un `UsageError`, o `None` |
| `signal`       | La primera señal de terminación recibida, o `None`                     |
| `clients`      | Un `ClientTiming` por cliente: duraciones y resultados de conexión y desconexión; vacío si la ejecución falló antes de conectar |

Los hooks son objetos comunes, no clientes: gestionan sus propios recursos. Una excepción en un hook
se registra en el log y no cambia el código de salida. `--help` no es una ejecución, así que los hooks no lo ven.

#### <a id="startup-metrics-and-structured-logs"></a>Métricas de arranque y logs estructurados

`run.clients` es el lugar para exportar métricas de arranque: `on_finish` ve cuánto tardó cada
cliente en conectarse y desconectarse. Además, cada registro de log de `nuke_di` lleva campos
estructurados, así que un formateador JSON puede filtrar y agregar por cliente sin analizar
los mensajes:

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

| Campo      | Presente en                                                                   |
|------------|-------------------------------------------------------------------------------|
| `run`      | Cada registro hecho dentro de un worker o un job, incluidos los del contenedor: el nombre de la ejecución |
| `client`   | Cada registro sobre un cliente: resolución, conexión, desconexión, fallos     |
| `layer`    | Cada registro sobre un cliente que se conecta o desconecta, y `Connecting layer` |
| `duration` | Segundos: un cliente conectado o desconectado, el resumen de arranque, una ejecución terminada |

Cada ejecución conecta también sus propios clientes `Shutdown` y `BackgroundTasks`, así que
aparecen en `run.clients` y en el resumen.

### <a id="running-in-kubernetes"></a>Ejecución en Kubernetes

Un job corresponde a un CronJob y un worker a un Deployment. Dale a un worker un
`terminationGracePeriodSeconds` suficiente para el [margen de apagado](#grace-period):

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

Una recarga puntual de datos históricos (backfill) es la misma imagen con otros parámetros:

```bash
kubectl run sync-backfill --rm -it --restart=Never --image=registry.example.com/app:1.0 \
  --command -- python -m app.jobs.sync --day 2026-09-30 --mode FULL
```

## <a id="fastapi"></a>FastAPI

Una operación de path de FastAPI recibe un cliente igual que un job: por su type hint. No hay que
escribir nada más en cada handler: ni `Depends`, ni `inject()`.

```bash
pip install "nuke-di[fastapi]"
```

Requiere FastAPI 0.105 o posterior. Los ejemplos comparten un mismo módulo de clientes:

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

La API:

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

Qué pasó:

1. `setup(app)` hizo que cada ruta declarada después en `app` rellene sus argumentos de tipo cliente
   desde el `DI` global, y envolvió el lifespan de la app.
2. `@app.get` vio `users: UserService` y solo lo anotó; al importar no se construyó nada.
3. Al arrancar, el lifespan resolvió los clientes de las rutas que sirve la app, las propias y las de
   los routers que incluye, y los conectó capa por capa. Al apagarse, los desconectó.
4. Una petición a `/users/42` recibió el `UserService` ya conectado. `/me` pasó por la dependencia
   `current_user`, que recibe `db: Database` de la misma manera.

Las reglas:

- **Dónde se rellenan los clientes.** En los argumentos de las operaciones de path, de los endpoints
  websocket y de cada dependencia que usen, a cualquier profundidad: funciones y clases usadas como `Depends(Auth)` o `Annotated[Auth, Depends()]`,
  incluidas las `dependencies=` de la ruta, de su router, de `include_router()` y de la app. Un
  argumento es un cliente cuando su type hint es un cliente, también dentro de `Annotated[UserService, ...]`
  sin `Depends`. Cualquier otro argumento es de FastAPI: path, query, header, body, `Depends`.
- **Routers.** Créalos con `ClientRouter(...)`, que acepta los mismos argumentos que `APIRouter`,
  e inclúyelos en la app o en otro `ClientRouter`. `APIRouter(route_class=ClientRoute)`
  funciona para un router que no incluye otros routers. Para otro contenedor, usa
  `setup(app, container)` y `ClientRouter(container=container)`; incluir un router de otro
  contenedor lanza `TypeError` de inmediato.
- **Llama a `setup(app)` antes de las rutas.** Una ruta con un cliente declarada antes falla de inmediato
  con el `TypeError` descrito [más abajo](#not-supported).
- **Solo lo que sirve la app.** Un router que la app no incluye, por ejemplo uno que solo importa una
  prueba, no conecta nada al arrancar la app.
- **Instancias.** Igual que con `inject()`, un `Client` es una única instancia por contenedor, y un
  `NotSingletonClient` es una instancia por cada argumento que lo declara, no una por petición.
- **Lifespan.** El `lifespan=` propio de la app se ejecuta por dentro: su código de arranque ve los
  clientes ya conectados, y su código de apagado se ejecuta antes de que se desconecten. Al apagarse,
  se activa `Shutdown` y se detienen las `BackgroundTasks`, si la app las usa, antes de que los clientes
  se desconecten, igual que en un worker. El `BackgroundTasks` propio de FastAPI es otra clase y no es un cliente.
- **La función sigue siendo una función.** Ahora su firma le muestra a FastAPI `Annotated[UserService, Depends(...)]`,
  pero llamarla directamente con un cliente, por ejemplo en una prueba unitaria, funciona como siempre.

**Pruebas.** Importar la app no construye nada, así que una prueba reemplaza un cliente antes de que
`TestClient` arranque la app, con [`override()`](#testing) o con el fixture `global_di`:

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

`app.dependency_overrides` sigue funcionando, también para una función de dependencia que recibe clientes.

**Websockets.** Un endpoint websocket recibe clientes de la misma manera, en la app o en un `ClientRouter`:

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

**Un cliente que no logra conectarse** hace fallar el arranque. El lifespan lanza un `RuntimeError`
simple a partir del `ConnectError`, ya que un `SystemExit` escaparía del event loop del servidor, y el
servidor lo informa y termina:

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

### <a id="not-supported"></a>No admitido

Estos lugares no aceptan clientes. Cada uno lanza un `TypeError` que lo indica al declarar la ruta:

| Lugar                                                       | Alternativa                                   |
|-------------------------------------------------------------|-----------------------------------------------|
| Un router creado sin `ClientRouter` / `ClientRoute`         | Créalo con `ClientRouter(...)`                |
| Un endpoint websocket en `APIRouter(route_class=ClientRoute)` | Crea el router con `ClientRouter(...)`      |
| Un cliente opcional, `Database \| None`                     | Un `Database` a secas                         |
| Un método vinculado o un objeto invocable como endpoint o dependencia | Una función o una clase             |

A diferencia de estos casos, una ruta de un router incluido en un `APIRouter` común en lugar de en un
`ClientRouter` solo la detectan las versiones antiguas de FastAPI. En FastAPI 0.14x se declara y la app
arranca, pero sus peticiones fallan con `RuntimeError: UserService was not started with the app: include the router of its route into
the app or into a ClientRouter, not into a plain APIRouter`.

Una petición que llega sin el lifespan, por ejemplo a través de `TestClient(app)` sin `with`, recibe un
`RuntimeError`: `UserService is not connected: start the app with its lifespan`.

## <a id="litestar"></a>Litestar

Un route handler de Litestar también recibe un cliente por su type hint, a través de un plugin:

```bash
pip install "nuke-di[litestar]"
```

Requiere Litestar 2.15 o posterior. Con los clientes de los ejemplos de [FastAPI](#fastapi):

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

`ClientPlugin()` encontró `users: UserService` en `get_user` y `db: Database` en la dependencia
`current_user`, se los proporcionó a Litestar como dependencias y los conectó al arrancar.

Las reglas:

- **Dónde se rellenan los clientes.** En los argumentos de los handlers HTTP y `@websocket` con los que se
  crea la app, incluidos los de routers y controllers a cualquier profundidad, y de cada dependencia
  declarada en la app, en un router, en un controller o en un handler: funciones y clases.
- **Por nombre.** Litestar proporciona las dependencias por nombre de argumento, así que nuke-di
  proporciona cada argumento de tipo cliente con su nombre, en la app. Un nombre equivale a un cliente
  en toda la app: `users: UserService` en un handler y `users: Billing` en otro lanzan `TypeError` al
  crear la app. Una dependencia con el mismo nombre declarada por la app, un router, un controller o
  un handler tiene prioridad sobre el cliente.
- **Instancias.** Un `Client` es una única instancia por contenedor; un `NotSingletonClient` es una
  instancia por nombre de argumento.
- **Lifespan.** Los clientes se conectan antes de que se ejecuten el `lifespan=` y los `on_startup=`
  propios de la app, y se desconectan después de sus hooks `on_shutdown=`, que Litestar llama al final.
  `Shutdown` y `BackgroundTasks` se comportan igual que en [FastAPI](#fastapi).
- **La función sigue siendo una función.** Sus argumentos de tipo cliente ahora están anotados como
  dependencias explícitas de Litestar cuyo valor no se valida,
  `Annotated[UserService, Dependency(), SkipValidationMarker()]`, que es lo que pide Litestar 2.23 en
  lugar de una dependencia emparejada solo por nombre. Llamar a la función directamente funciona como
  siempre.
- **Plugins.** Pon `ClientPlugin()` después de cualquier plugin que añada route handlers: ve los handlers
  que tiene la app cuando le llega su turno.
- **Otro contenedor.** `ClientPlugin(container)`.

**Pruebas.** Igual que con FastAPI, una prueba reemplaza un cliente antes de que `TestClient` arranque la app:

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

**No admitido.** Un websocket listener, `@websocket_listener` o una clase `WebsocketListener`, no acepta
clientes: Litestar lee su firma en el momento de declararlo, antes de que el plugin lo vea, así que la
app lanza `TypeError` y sugiere en su lugar un handler `@websocket`. Un argumento de tipo cliente con un
nombre que Litestar reserva, como `state` o `request`, también lanza `TypeError`. Un handler registrado
después de crear la app, con `app.register()`, no se ve.

## <a id="faststream"></a>FastStream

Un subscriber de FastStream recibe un cliente por su type hint, junto al mensaje:

```bash
pip install "nuke-di[faststream]"
```

Requiere FastStream 0.6 o posterior, con cualquier broker. Con los clientes de los ejemplos de [FastAPI](#fastapi):

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

El mensaje se publicó con:

```python
# publish.py
import asyncio

from faststream.nats import NatsBroker


async def main() -> None:
    async with NatsBroker("nats://localhost:4222") as broker:
        await broker.publish(42, "greetings")


asyncio.run(main())
```

Las reglas:

- **Dónde se rellenan los clientes.** En los argumentos de los subscribers de los brokers de la app,
  también los de los routers incluidos, y de cada `Depends(...)` que usen, a cualquier profundidad:
  funciones y clases, incluidas las `dependencies=` del subscriber, de su router y del broker. Cualquier
  otro argumento es de FastStream: el mensaje, sus campos, `Context()`.
- **Qué clientes arrancan.** Al arrancar, los de cada subscriber que sirven los brokers de la app,
  routers incluidos. Los subscribers pueden declararse antes o después de `setup(app)`.
- **Lifespan.** Los clientes se conectan antes de los hooks `lifespan=` y `on_startup=` propios de la app
  y antes de que arranquen los brokers; se desconectan después de que los brokers se detengan y después
  de los hooks `after_shutdown=`. `Shutdown` y `BackgroundTasks` se comportan igual que en
  [FastAPI](#fastapi). `setup()` también funciona con una `AsgiFastStream`.
- **Instancias.** Igual que con `inject()`, un `Client` es una única instancia por contenedor, y un
  `NotSingletonClient` es una instancia por cada argumento que lo declara, no una por mensaje.
- **La función sigue siendo una función.** Su firma le muestra a FastStream `Annotated[UserService, Depends(...)]`,
  igual que en [FastAPI](#fastapi).
- **Una app a la vez.** Una función subscriber y sus dependencias se reescriben una sola vez, sea cual sea el
  contenedor, así que las apps que las comparten, p. ej. una app por test sobre un broker a nivel de módulo,
  se ejecutan una tras otra: una app que arranca mientras corre otra con la misma función no logra arrancar.
  Una función de dependencia que recibe clientes sirve handlers de FastAPI o de FastStream, no de ambos.

**Pruebas.** El broker de pruebas de FastStream no ejecuta los hooks de la app, así que arranca la app
con `TestApp` dentro de él:

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

Un mensaje procesado sin el lifespan de la app, por ejemplo a través de `TestNatsBroker(broker)` sin
`TestApp`, lanza `RuntimeError: UserService is not connected: start the app with its lifespan`. Un
subscriber añadido después de que la app haya arrancado lanza
`RuntimeError: UserService was not started with the app`.

## <a id="testing"></a>Pruebas

**Un cliente a través de un contenedor.** Registra los mocks antes de resolver el árbol; a partir de
ahí, cada consumidor recibe el mock:

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

**Un cliente para un bloque, con `override()`.** `override(cls, new=None)` registra un Reemplazo
igual que `mock()`, pero dura hasta el final del bloque `with`, incluso a lo largo de varios ciclos
de `async with`, y el contenedor se vacía al salir, así que nada de lo que se resolvió con él se filtra
a la siguiente prueba. También funciona con el `DI` global:

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

Las pruebas asíncronas de esta sección usan [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) con
`asyncio_mode = auto` en `pytest.ini`; sin él, pytest no ejecuta las pruebas `async def`.

```console
$ pytest -q -s test_greet.py
Hello, alice!
after the block: OrderedDict()
.Hello, bob!
.
2 passed in 0.01s
```

`database: connected` nunca se imprime: un Reemplazo no se conecta.

Las reglas:

- **Reemplaza antes de resolver.** Un Reemplazo registrado después de resolver `cls` solo llegaría
  a los consumidores resueltos más tarde, mientras que los anteriores conservarían el cliente real, así
  que `mock()` lanza una excepción:

  ```python
  DI.inject(handler)  # resolves UserService -> Database
  DI.mock(Database)  # ConnectError: Database is already resolved, call mock() before resolve() or inject()
  ```

- **`override()` parte de un contenedor sin clientes resueltos.** De lo contrario, el vaciado al salir
  descartaría en silencio lo que se resolvió antes del bloque, así que lanza
  `ConnectError: override(Database) needs a container without resolved clients, found: Database, UserService`.
  Llama primero a `DI.flush()` o usa el fixture `global_di` que se describe más abajo.
- **Un Reemplazo por clase.** Si se vuelve a llamar a `mock(cls)`, devuelve el Reemplazo ya
  registrado; `mock(cls, other)` y `override(cls)` lanzan `ConnectError: Database already has a
  replacement`.
- **Los Reemplazos no se conectan.** Nunca se llama a su `connect()` / `disconnect()`, y no
  participan en las [capas](#layers).
- **Cuánto dura un Reemplazo.** El de `mock()` se descarta con el siguiente `flush()`, incluido el
  que hay al final de `disconnect()`: una prueba que conecta el contenedor más de una vez debería usar
  `override()`, cuyo Reemplazo sobrevive a cada `flush()` hasta que termina su bloque. Una excepción dentro
  del bloque se propaga sin cambios; salir del bloque normalmente mientras el contenedor sigue conectado
  lanza `ConnectError`.
- **Anidamiento.** Los bloques para clases distintas se pueden anidar siempre que cada uno se abra antes
  de resolver nada, por ejemplo `with DI.override(Database), DI.override(Clock):`; al salir del bloque
  interior se conserva el Reemplazo del exterior.

**Fixtures de pytest.** Al instalar `nuke-di` se registra un plugin de pytest con dos fixtures. Ninguno
es autouse, así que las pruebas existentes se ejecutan exactamente igual que antes:

| Fixture     | Proporciona                                                   |
|-------------|---------------------------------------------------------------|
| `di`        | Un `Dependencies` nuevo para una prueba                       |
| `global_di` | El `DI` global, vaciado antes y después de la prueba          |

Una prueba que deja el contenedor conectado recibe un error en el teardown, y el contenedor se vacía
igualmente, así que la siguiente prueba empieza limpia:

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

Los fixtures no pueden desconectar por sí mismos un contenedor olvidado: para cuando llega el teardown,
el event loop de la prueba puede estar cerrado. `global_di` solo protege a las pruebas que lo piden: una
prueba que usa el `DI` global sin él todavía puede dejar clientes colgados para la siguiente. Un proyecto
que define su propio fixture `di` lo conserva, ya que un fixture de `conftest.py` tiene prioridad sobre
el de un plugin; `pytest -p no:nuke_di` desactiva el plugin.

**Un job, directamente.** Importar el módulo no ejecuta el job, así que llama a la función con
mocks y parámetros:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    pg.upsert.assert_awaited_once_with("users", ["row"])
```

**Un job a través de un contenedor**, con los clientes conectados entre sí como en producción:

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

**Un worker.** `Shutdown.set()` hace lo mismo que haría SIGTERM:

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

## <a id="configuration"></a>Configuración

| Variable de entorno          | Por defecto | Descripción                                        |
|------------------------------|-------------|----------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`        | Timeout del `connect()` de cada cliente, en segundos |
| `CONNECT_CONCURRENCY`        | `0`         | Cuántos clientes pueden conectarse o desconectarse a la vez en todo el contenedor; `0` significa sin límite |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`        | Timeout del `disconnect()` de cada cliente, en segundos |
| `SHUTDOWN_GRACE_SECONDS`     | `10`        | Cuánto tiempo puede seguir ejecutándose un worker o un job tras SIGTERM / SIGINT antes de cancelarse, en segundos; se lee al arrancar el proceso |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

La configuración del contenedor se lee al crear una instancia de `Dependencies`. También se puede
pasar de forma explícita:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```

## <a id="errors"></a>Errores

| Excepción                   | Se lanza cuando                                           |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | El `__init__` de un cliente lanzó una excepción           |
| `ConnectError`              | El `connect()` de un cliente lanzó una excepción, o el estado del contenedor no es el correcto (por ejemplo, resolver después de conectar, mockear un cliente que ya está resuelto o hacer override de un contenedor que tiene clientes resueltos) |
| `ConnectTimeoutError`       | El `connect()` de un cliente superó `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError`     | El `__init__` de un cliente tiene un argumento obligatorio que no es un cliente, `inject()` recibió una función con un argumento sin type hint, o un parámetro de un punto de entrada tiene un tipo no admitido o un flag en conflicto; consulta [Cuando el árbol no se puede construir](#when-the-tree-cannot-be-built) |
| `CircularDependencyError`   | Hay clientes que dependen unos de otros en un ciclo; es una subclase de `InvalidSignatureError` |
| `UsageError`                | La línea de comandos de un worker o un job no coincide con sus parámetros; se guarda como `Run.error`, código de salida `2` |

`InitializeDependencyError` y `ConnectError` derivan de `SystemExit`: se espera que una aplicación
cuyas dependencias no pueden arrancar se detenga. Captúralas de forma explícita si necesitas
otro comportamiento; la excepción original está disponible en `__cause__`.

`nuke-di` escribe sus logs con el módulo estándar `logging`, en el logger `nuke_di`, con
[campos estructurados](#startup-metrics-and-structured-logs) para los pipelines de logs.

## <a id="development"></a>Desarrollo

```bash
make install   # uv sync --locked
make check     # ruff, mypy and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

La cobertura de líneas y de ramas es del 100 %, y la CI falla si baja de ese valor
(`fail_under = 100` en `pyproject.toml`).

### <a id="releases"></a>Publicación de versiones

Cada merge en `master` es una nueva versión publicada. El workflow `Release` publica en PyPI la versión
indicada en `pyproject.toml`, crea la etiqueta `vX.Y.Z` y crea una release de GitHub a partir de su sección de
`CHANGELOG.md`. Por eso cada pull request lleva su propia versión: súbela con `uv version --bump
patch|minor|major` y convierte `## [Unreleased]` en `## [X.Y.Z] - YYYY-MM-DD`, con un enlace de comparación al
final. La CI lo comprueba en cada pull request, y `make check-version` lo comprueba en local:

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

Un cambio que llega a `master` sin una nueva versión, por ejemplo con un push directo, hace fallar el workflow
`Release` antes de que se construya o se publique nada.

## <a id="license"></a>Licencia

[MIT](../../LICENSE)
