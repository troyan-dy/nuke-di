# <a id="faststream"></a>FastStream

[English](../../guide/faststream.md) · [Русский](../ru/faststream.md) · [简体中文](../zh-CN/faststream.md) · **Español** · [Português (Brasil)](../pt-BR/faststream.md) · [日本語](../ja/faststream.md) · [Polski](../pl/faststream.md)

← [Documentación](../README.es.md#documentation)

Un subscriber de FastStream recibe un cliente por su type hint, junto al mensaje:

```bash
pip install "nuke-di[faststream]"
```

Requiere FastStream 0.6 o posterior, con cualquier broker. Con los clientes de los ejemplos de [FastAPI](fastapi.md):

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
  [FastAPI](fastapi.md). `setup()` también funciona con una `AsgiFastStream`.
- **Instancias.** Igual que con `inject()`, un `Client` es una única instancia por contenedor, y un
  `NotSingletonClient` es una instancia por cada argumento que lo declara, no una por mensaje.
- **La función sigue siendo una función.** Su firma le muestra a FastStream `Annotated[UserService, Depends(...)]`,
  igual que en [FastAPI](fastapi.md).
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
