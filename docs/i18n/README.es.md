# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](es/development.md)
[![License](https://img.shields.io/pypi/l/nuke-di)](../../LICENSE)

[English](https://github.com/troyan-dy/nuke-di/blob/master/README.md) · [Русский](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ru.md) · [简体中文](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.zh-CN.md) · **Español** · [Português (Brasil)](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pt-BR.md) · [日本語](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ja.md) · [Polski](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pl.md)

La inyección de dependencias más sencilla para proyectos asíncronos en Python.

Las dependencias se declaran con type hints normales y corrientes. `nuke-di` construye el árbol
de dependencias, crea cada cliente una sola vez y gestiona su ciclo de vida asíncrono: `connect()`
al arrancar y `disconnect()` al apagarse. Cada cliente arranca en cuanto sus propias dependencias se
han conectado, de forma concurrente con todos los demás clientes que estén listos.

Además, un solo decorador convierte una función asíncrona en un proceso con argumentos de línea de
comandos, y los handlers de FastAPI, Litestar y FastStream reciben clientes por su type hint de la misma manera.

Nació a partir de la capa de DI de un framework de microservicios en Python usado en producción
y no tiene dependencias en tiempo de ejecución.

- [Instalación](#installation) · [Inicio rápido](#quick-start) · [Principios](#principles) · [Rendimiento](#performance)
- Ejemplos: [un job con argumentos de línea de comandos](#a-job-with-command-line-arguments) · [FastAPI](#fastapi)
- [Documentación](#documentation)

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

## <a id="principles"></a>Principios

- **Una dependencia es una clase.** Una subclase de `Client` con un `__init__` con type hints y
  `connect()` / `disconnect()` asíncronos es todo el modelo: sin providers, sin módulos, sin registro,
  sin scopes que configurar. Un objeto de terceros se convierte en dependencia envolviéndolo en una clase así.
- **Los type hints son el cableado.** Un cliente pide sus dependencias en `__init__`; una función, en
  su firma. Nada más las nombra, así que renombrar o añadir una dependencia es una refactorización corriente.
- **Arranque concurrente, apagado ordenado.** Un cliente se conecta en cuanto sus propias dependencias
  lo han hecho, de forma concurrente con todos los demás clientes que estén listos, así que un cliente
  lento solo retrasa a los clientes que lo necesitan. Se desconectan en orden inverso, y un
  `disconnect()` que falla no detiene a los demás.
- **Fallar pronto.** Un árbol que no se puede construir falla antes de que se conecte nada, indicando el
  argumento y la ruta hasta él, y `mypy` con el [plugin](es/clients.md#checking-the-tree-with-mypy)
  informa del mismo error antes de que arranque el proceso. Un cliente que no logra conectarse detiene
  la aplicación una vez que se desconectan los que ya se habían conectado. No hay reintentos: reiniciar
  es tarea del orquestador.
- **Las pruebas reemplazan, no recablean.** `mock()` y `override()` ponen un objeto falso en lugar de un
  cliente durante una prueba; el código bajo prueba no cambia.
- **Sin dependencias en tiempo de ejecución.** El núcleo usa solo la biblioteca estándar; las
  integraciones con frameworks son extras.

Así transcurre un arranque, con el ejemplo de la [guía de clientes](es/clients.md#connect-order): `Consumer`
solo necesita `Kafka`, así que no espera al lento `Postgres`, y el arranque dura lo que su cadena de
dependencias más larga.

![Seis clientes que se conectan por sus propias dependencias: Consumer y Http arrancan en cuanto Kafka y Redis se han conectado, el arranque tarda 0.35s](https://raw.githubusercontent.com/troyan-dy/nuke-di/66f74f76407da320cfc97ef22b761d85e298eddd/docs/connect-now.svg)

## <a id="performance"></a>Rendimiento

`benchmarks/compare.py` pasa los mismos árboles de clientes por dishka, wireup, dependency-injector e
injector, registrando las mismas clases como lo hace cada biblioteca: un contenedor frío con la raíz
resuelta, sobre clases nuevas para el proceso, la raíz de nuevo y una petición FastAPI a través de la
integración de cada biblioteca:

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

Entonces, ¿es `nuke-di` la más rápida? Al construir un árbol con anotaciones de tipo reales y en una
petición FastAPI, sí: dependency-injector e injector tardan 2–2,5 veces más en el árbol, dishka y wireup
24–37 veces más por validar el grafo al crear el contenedor, y wireup y dependency-injector el doble por
petición. Con anotaciones en cadena, dependency-injector, que no lee ninguna anotación, va una quinta parte
por delante. En una raíz en caché `nuke-di` va a la par con wireup, y el `get()` en Cython de
dependency-injector gana por unos 50 ns, una diferencia que ninguna aplicación nota.

Por sí solo, `resolve()` cuesta 3,5–6,3 µs por cliente, así que un árbol de 1000 clientes se construye en
menos de 5,5 ms, y `connect()` añade 13–18 µs por cliente. [docs/benchmarks.md](../benchmarks.md)
explica cada escenario, registra la línea base en Python 3.11–3.14 y contiene la comparación completa con
su método.

Con conexiones reales el coste es la espera, y lo que decide un arranque es cuándo empieza cada `connect()`.
dishka y wireup conectan un cliente tras otro dentro de un `get()`, así que su arranque es la suma de todos
los `connect()` salvo que la aplicación reúna sus ramas a mano;
dependency-injector arranca tan en concurrencia como `nuke-di` cuando cada cliente es un `Resource` escrito a
mano, y se detiene por capas; injector no tiene ciclo de vida asíncrono:

```console
$ uv run python benchmarks/compare.py --only connect --summary
nuke-di 1.14.2 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 8d0700b · N = 10, 100, 1000 · 20 repeats
nuke-di 1.14.2 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

| Lower is better                                     | nuke-di     | dishka         | wireup         | dependency-injector | injector |
|-----------------------------------------------------|------------:|---------------:|---------------:|--------------------:|---------:|
| Startup: 8 clients, connect() of 1–60 ms            | **70.8 ms** | 156 ms (2.2×)  | 156 ms (2.2×)  | 71.1 ms (1.0×)      | —        |
| Shutdown: the same 8 clients                        | **18.7 ms** | 30.1 ms (1.6×) | 29.8 ms (1.6×) | 26.0 ms (1.4×)      | —        |
| Startup: 10 independent clients, connect() of 50 ms | **52.3 ms** | 520 ms (10.0×) | 522 ms (10.0×) | 52.5 ms (1.0×)      | —        |
```

Ninguna de las tres conecta nada al crear su contenedor: si la aplicación no obtiene la raíz al arrancar,
su primera petición espera a las conexiones y falla con ellas. `nuke-di` conecta cada cliente en
`async with DI`, y un cliente que no puede conectarse detiene el arranque.

## <a id="a-job-with-command-line-arguments"></a>Un job con argumentos de línea de comandos

Un solo decorador convierte una función asíncrona en el programa principal de un proceso. Los clientes
se inyectan, y todo otro argumento anotado se convierte en una opción de línea de comandos, tipada y
validada:

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

Sin `main()`, sin `asyncio.run()`, sin `argparse`: el decorador resuelve y conecta los clientes, analiza
la línea de comandos, ejecuta la función y termina con un código de salida con significado:

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

`--help` se genera a partir de la firma y del docstring (Python 3.13+ muestra `-d, --day DAY` en lugar de
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

Una línea de comandos incorrecta se rechaza antes de conectar ningún cliente, con el código de salida `2`:

```console
$ python sync.py -d 2026-10-01 --mode full
usage: sync.py [-h] -d DAY [-t TABLES] [--mode {INCREMENTAL,FULL}]
               [--dry-run | --no-dry-run]
sync.py: error: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
Run sync.sync failed: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
$ echo $?
2
```

`@worker` hace lo mismo para un proceso que se ejecuta hasta recibir SIGTERM, con un apagado ordenado.
Ambos se describen en [Workers y jobs](es/workers-and-jobs.md).

## <a id="fastapi"></a>FastAPI

Una operación de path recibe un cliente por su type hint, sin `Depends` ni `inject()` en cada handler.
`app/clients.py` contiene las clases `Database` y `UserService` del [Inicio rápido](#quick-start), sin su
`main()`:

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

Los clientes se conectan al arrancar y se desconectan al apagarse, y una dependencia como `current_user`
recibe clientes de la misma manera. Importar la app no construye nada, así que una prueba reemplaza un
cliente antes de que `TestClient` la arranque:

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

Los routers, los websockets y el lifespan propio de la app se describen en [FastAPI](es/fastapi.md);
[Litestar](es/litestar.md) y [FastStream](es/faststream.md) funcionan de la misma manera.

## <a id="documentation"></a>Documentación

- [Clientes](es/clients.md): `Client` y `NotSingletonClient`, el ciclo de vida, clientes como dataclass,
  orden de conexión, tiempos de arranque, el grafo de dependencias, errores de conexión y de resolución
- [El contenedor](es/container.md): `Dependencies` y el `DI` global, `resolve()`, `inject()`,
  `mock()`, `override()`
- [Workers y jobs](es/workers-and-jobs.md): `@job` y `@worker`, parámetros de línea de comandos,
  `Shutdown`, el periodo de gracia, tareas en segundo plano, códigos de salida, hooks, Kubernetes
- Frameworks: [FastAPI](es/fastapi.md), [Litestar](es/litestar.md),
  [FastStream](es/faststream.md), y
  [escribir una integración](es/integrations.md) para otro framework con `nuke_di.integration`
- [Pruebas](es/testing.md): `mock()`, `override()`, los fixtures de pytest, la comprobación del cableado
- [Configuración](es/configuration.md): timeouts, concurrencia y el periodo de gracia
- [Errores](es/errors.md): cada excepción y cuándo se lanza
- [Ejemplos](../../examples/README.md): 21 escenarios listos para ejecutar, desde un script puntual y un worker de colas
  hasta FastAPI, Litestar, FastStream, Starlette y un servicio completo, cada uno con su salida y sus pruebas
- [Benchmarks](../benchmarks.md): cada escenario, la línea base en Python 3.11–3.14 y la comparación
  con otras bibliotecas
- [Agentes de programación](es/agents.md): la Agent Skill, un bloque para `AGENTS.md`,
  [`llms.txt`](https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms.txt), Context7, el grafo como JSON
- [Desarrollo](es/development.md): las comprobaciones, la cobertura y la publicación de versiones

## <a id="license"></a>Licencia

[MIT](../../LICENSE)
