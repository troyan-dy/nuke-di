# <a id="workers-and-jobs"></a>Workers y jobs

[English](../../guide/workers-and-jobs.md) · [Русский](../ru/workers-and-jobs.md) · [简体中文](../zh-CN/workers-and-jobs.md) · **Español** · [Português (Brasil)](../pt-BR/workers-and-jobs.md) · [日本語](../ja/workers-and-jobs.md) · [Polski](../pl/workers-and-jobs.md)

← [Documentación](../README.es.md#documentation)

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

## <a id="your-first-job"></a>Tu primer job

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
INFO  nuke_di.core: Connected 4 clients in 0.00s (slowest: Postgres 0.00s, Shutdown 0.00s, Warehouse 0.00s)
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
INFO  nuke_di.run: Run app.jobs.sync.sync finished with exit code 0 in 0.002s
```

### <a id="one-entrypoint-per-module-defined-last"></a>Un punto de entrada por módulo, definido al final

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

## <a id="parameters"></a>Parámetros

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

### <a id="supported-types"></a>Tipos admitidos

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

### <a id="parameters-in-tests"></a>Parámetros en las pruebas

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

## <a id="your-first-worker"></a>Tu primer worker

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

Un servidor sin inyección de dependencias propia, como un servidor gRPC, una app de aiohttp o un worker de
Temporal, se ejecuta dentro de un worker de la misma manera: consulta [Servidores dentro de un worker](servers-in-workers.md).

En Windows solo se maneja SIGINT (Ctrl+C); SIGTERM conserva su comportamiento por defecto.

## <a id="grace-period"></a>Periodo de gracia

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
`SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × la cadena de dependencias más larga`. Con los
valores por defecto, una cadena de dos clientes consume todo el `terminationGracePeriodSeconds` por defecto de Kubernetes, de 30 segundos,
así que para árboles más profundos reduce los timeouts o aumenta el periodo de gracia.

## <a id="background-tasks"></a>Tareas en segundo plano

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

## <a id="exit-codes"></a>Códigos de salida

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

## <a id="hooks"></a>Hooks

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

### <a id="startup-metrics-and-structured-logs"></a>Métricas de arranque y logs estructurados

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

| Campo      | Presente en                                                                   |
|------------|-------------------------------------------------------------------------------|
| `run`      | Cada registro hecho dentro de un worker o un job, incluidos los del contenedor: el nombre de la ejecución |
| `client`   | Cada registro sobre un cliente: resolución, conexión, desconexión, fallos     |
| `duration` | Segundos: un cliente conectado o desconectado, el resumen de arranque, una ejecución terminada |

Cada ejecución conecta también sus propios clientes `Shutdown` y `BackgroundTasks`, así que
aparecen en `run.clients` y en el resumen.

## <a id="running-in-kubernetes"></a>Ejecución en Kubernetes

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
      terminationGracePeriodSeconds: 30  # >= SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × longest chain
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
