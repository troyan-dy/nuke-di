# <a id="testing"></a>Pruebas

[English](../../guide/testing.md) · [Русский](../ru/testing.md) · [简体中文](../zh-CN/testing.md) · **Español** · [Português (Brasil)](../pt-BR/testing.md) · [日本語](../ja/testing.md) · [Polski](../pl/testing.md)

← [Documentación](../README.es.md#documentation)

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
  participan en el [orden de conexión](clients.md#connect-order).
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

**Cada entrypoint se resuelve.** Importar un módulo no ejecuta su job ni su worker, e `inject()`
construye el árbol sin conectar nada, así que una sola prueba comprueba el cableado de cada entrypoint
en CI: un ciclo, un argumento sin anotación de tipo, un argumento obligatorio que no es un cliente o un
`__init__` que lanza la hacen fallar con el mismo error que imprimiría una ejecución real, y no hace
falta ninguna base de datos:

```python
# test_wiring.py
from collections.abc import Callable

import pytest

from nuke_di import Dependencies

from app.jobs import sync
from app.workers import consumer


@pytest.mark.parametrize("entrypoint", [sync.sync, consumer.consumer])
def test_entrypoint_resolves(entrypoint: Callable[..., object]) -> None:
    Dependencies().inject(entrypoint)  # runs every __init__, connects nothing
```

```console
$ pytest -q test_wiring.py
..                                                                       [100%]
2 passed in 0.05s
```

Conserva el contenedor para obtener [el grafo](clients.md#the-graph) de un entrypoint para su README:
`deps = Dependencies(); deps.inject(sync.sync); print(deps.graph().to_mermaid())`.

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
