# <a id="taskiq"></a>taskiq

[English](../../guide/taskiq.md) · [Русский](../ru/taskiq.md) · [简体中文](../zh-CN/taskiq.md) · **Español** · [Português (Brasil)](../pt-BR/taskiq.md) · [日本語](../ja/taskiq.md) · [Polski](../pl/taskiq.md)

← [Documentación](../README.es.md#documentation)

Una tarea de taskiq recibe un cliente por su type hint, junto a los argumentos con los que se encola:

```bash
pip install "nuke-di[taskiq]"
```

Requiere taskiq 0.11 o posterior, con cualquier broker. Con los clientes de los ejemplos de [FastAPI](fastapi.md)
y un `InMemoryBroker`, que ejecuta las tareas en el mismo proceso que las encola:

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

**Verificadores de tipos.** taskiq tipa `.kiq()` con la firma de la propia tarea, así que mypy y pyright
piden `users` en `send_report.kiq(42)`; lo mismo vale para cualquier argumento que rellene taskiq, con
`Annotated` o sin él. Un valor por defecto hace que el argumento sea opcional para ellos, y el cliente sigue
llegando del contenedor por su tipo:

```python
@broker.task
async def send_report(user_id: int, users: UserService = TaskiqDepends()) -> None:
    print(await users.greet(user_id))
```

La regla `B008` de Ruff marca una llamada en un valor por defecto; los marcadores de taskiq son seguros ahí:

```toml
# pyproject.toml
[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["taskiq.TaskiqDepends"]
```

**Un worker de verdad.** En un despliegue el broker es el de una cola, p. ej. `NatsBroker` de
[taskiq-nats](https://github.com/taskiq-python/taskiq-nats); nada más cambia:

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

El proceso del worker conecta los clientes; un proceso que solo encola tareas, como una app web, conecta el
broker y nada más:

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

En otra terminal:

```console
$ python -m app.kick
kicked send_report(42)
```

Los dos minutos antes de `Stopping prefetching messages...` son cosa de taskiq: su proceso worker (taskiq 0.13
con taskiq-nats 0.7) se entera de la señal con el siguiente mensaje o ping de NATS.

Las reglas:

- **Dónde se rellenan los clientes.** En los argumentos de las tareas del broker y de cada función
  `TaskiqDepends(...)` que usen, a cualquier profundidad, dependencias generadoras incluidas. Cualquier otro
  argumento es de taskiq: los argumentos de `.kiq()`, `Context`, `TaskiqState`. Una clase de dependencia,
  `Annotated[Auth, TaskiqDepends()]`, la construye taskiq a partir de su propio `__init__`, que nuke-di no
  reescribe, así que una cuyo `__init__` recibe clientes se rechaza al registrar su tarea:
  `TypeError: Auth takes clients in __init__ and is a taskiq dependency`. Recibe un cliente por su type
  hint, sin `TaskiqDepends()`, y pásale a una clase que necesita clientes estos a través de una función de
  dependencia.
- **Qué clientes arrancan.** Los de cada tarea de `broker.get_all_tasks()`: las propias del broker y las
  compartidas (`@shared_task`). Las tareas del broker pueden declararse antes o después de `setup(broker)`.
  Una tarea compartida se registra en el broker compartido de taskiq, que `setup()` no intercepta: `setup()`
  reescribe las tareas compartidas que ya existen, y el arranque del worker el resto. `taskiq worker` lee
  las firmas de sus tareas antes de arrancar, así que allí una tarea compartida se importa antes de
  `setup()`; un `InMemoryBroker` la lee en su primera ejecución, así que allí vale cualquier orden.
- **Qué proceso conecta.** Aquel donde el broker dispara `WORKER_STARTUP`: un proceso de `taskiq worker` y
  cualquier proceso que arranque un `InMemoryBroker`, que es su propio worker. Un proceso que solo encola
  tareas arranca el broker con `CLIENT_STARTUP` y no conecta ningún cliente. Así, un mismo módulo con el
  broker sirve a ambos: el worker conecta a través de taskiq, la app web a través de su propia integración.
- **Lifespan.** Los clientes se conectan antes que los demás handlers de `WORKER_STARTUP`, incluidos los
  registrados antes de `setup()`, y se desconectan después de que `broker.shutdown()` haya ejecutado los
  handlers de `WORKER_SHUTDOWN`, los middlewares y el backend de resultados; en un `InMemoryBroker`, además,
  después de que terminen las tareas que siguen en marcha. `Shutdown` y `BackgroundTasks` se comportan igual
  que en [FastAPI](fastapi.md). `taskiq worker` le da a `broker.shutdown()` `--shutdown-timeout` segundos,
  5 por defecto, y cada `disconnect()` puede tardar `DISCONNECT_TIMEOUT_SECONDS`, 10 por defecto: pon
  `--shutdown-timeout` por encima de la cadena de desconexiones más larga, o una lenta se corta a medias.
- **Un arranque fallido.** Un `connect()` que falla hace fallar `broker.startup()` con un `RuntimeError`, y
  el proceso del worker muere. El gestor de procesos de taskiq lo reinicia sin fin por defecto
  (`--max-fails -1`), así que el orquestador nunca ve una caída y la dependencia caída se reintenta cada
  segundo. Ejecuta el worker con `--max-fails 1`: entonces sale con el código 255, y el orquestador lo
  reinicia con su propio backoff, ya que `connect()` es fail-fast en nuke-di.
- **Instancias.** Igual que con `inject()`, un `Client` es una única instancia por contenedor, y un
  `NotSingletonClient` es una instancia por cada argumento que lo declara, no una por tarea.
- **La función sigue siendo una función.** Su firma le muestra a taskiq
  `Annotated[UserService, TaskiqDepends(...)]`, igual que en [FastAPI](fastapi.md);
  `await send_report(1, users)` la llama con clientes pasados a mano. Una función que recibe clientes sirve
  a taskiq o a FastAPI: una ya vinculada a FastAPI se rechaza con un `TypeError` antes de registrar su tarea.
- **Un contenedor se conecta una vez.** Un `InMemoryBroker` arrancado en un proceso cuyo contenedor ya está
  conectado, p. ej. dentro de una app de FastAPI que corre sobre el mismo `DI`, falla con
  `RuntimeError: nuke-di clients failed to start: the container is already connected`. Dale a ese broker un
  contenedor propio: `setup(broker, container=Dependencies())`. Por la misma razón un worker no arranca con
  `taskiq_fastapi.init(broker, app)` para una app configurada con `nuke_di.fastapi` sobre el mismo
  contenedor: entra en el lifespan de la app en `WORKER_STARTUP`. Con `nuke_di.taskiq` las tareas reciben
  sus clientes sin él. Una función de tarea sirve a un broker a la vez, igual que un subscriber de FastStream.

**Pruebas.** Ejecuta las tareas sobre un `InMemoryBroker` y arráncalo dentro del override:

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

Una tarea que se ejecuta sin el arranque del worker, p. ej. encolada en un `InMemoryBroker` que nunca se
arrancó, falla con ``RuntimeError: UserService is not connected: the clients connect when the worker starts;
run tasks with `taskiq worker`, or start an InMemoryBroker with `await broker.startup()` before kicking them``
en su resultado. Una tarea declarada después de que arrancara el worker falla con
`RuntimeError: UserService was not started with the worker`.
