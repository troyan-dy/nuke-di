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

## <a id="publishing-from-a-client"></a>Publicar desde un cliente

Un cliente que publica, un outbox o un notificador, recibe el broker de la app en `__init__` y deja su
ciclo de vida a FastStream:

```python
# app/notify.py
from faststream import FastStream
from faststream.nats import NatsBroker

from app.clients import UserService
from nuke_di import Client
from nuke_di.faststream import setup

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
setup(app)


class Notifications(Client):
    # The app's broker: FastStream starts it after the clients connect and stops it before they
    # disconnect, so connect() and disconnect() leave it alone
    def __init__(self, nats: NatsBroker = broker) -> None:
        self._nats = nats

    async def send(self, text: str) -> None:
        await self._nats.publish(text, "notifications")


@broker.subscriber("greetings")
async def greet(user_id: int, users: UserService, notifications: Notifications) -> None:
    await notifications.send(await users.greet(user_id))


@broker.subscriber("notifications")
async def show(text: str) -> None:
    print(f"notification: {text}")
```

```console
$ faststream run app.notify:app
database: connected
2026-10-10 18:39:08,837 INFO     - FastStream app starting...
2026-10-10 18:39:08,842 INFO     - greetings     |            - `Greet` waiting for messages
2026-10-10 18:39:08,843 INFO     - notifications |            - `Show` waiting for messages
2026-10-10 18:39:08,843 INFO     - FastStream app started successfully! To exit, press CTRL+C
2026-10-10 18:39:11,811 INFO     - greetings     | 7c3cf44a-9 - Received
2026-10-10 18:39:11,811 INFO     - greetings     | 7c3cf44a-9 - Processed
2026-10-10 18:39:11,812 INFO     - notifications | 5ddab782-7 - Received
notification: Hello, user-42!
2026-10-10 18:39:11,812 INFO     - notifications | 5ddab782-7 - Processed
^C
2026-10-10 18:39:12,908 INFO     - FastStream app shutting down...
2026-10-10 18:39:12,909 INFO     - FastStream app shut down gracefully.
database: disconnected
```

El mensaje se publicó con el `publish.py` de arriba. En una prueba, el broker de pruebas enruta lo que
publica el cliente como cualquier otro mensaje, o se reemplaza el cliente:

```python
# tests/test_notify.py
from faststream import TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.notify import Notifications, app, broker, show
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


class FakeNotifications(Notifications):
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


async def test_greet_publishes() -> None:
    with DI.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(1, "greetings")

            show.mock.assert_called_once_with("Hello, alice!")


async def test_greet_with_fake_notifications() -> None:
    fake = FakeNotifications()
    with DI.override(Notifications, fake):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(42, "greetings")

    assert fake.sent == ["Hello, user-42!"]
```

```console
$ pytest -q tests/test_notify.py
..                                                                       [100%]
2 passed in 0.19s
```

Las reglas:

- **El broker es de la app.** FastStream lo arranca después de que se conectan los clientes y lo detiene
  antes de que se desconecten. Un cliente publica desde sus métodos mientras la app corre; un `publish()`
  en su `connect()` o `disconnect()` lanza `faststream.exceptions.IncorrectState`, ya que el broker todavía
  no ha arrancado, o ya se detuvo.
- **El broker es un argumento con valor por defecto**, no un cliente: nuke-di rellena los argumentos
  tipados como clientes y deja los demás con sus valores por defecto. Una prueba unitaria puede construir
  `Notifications(nats=AsyncMock())`.
- **Bajo `TestNatsBroker`** se parchea el mismo objeto broker, así que el cliente publica en memoria y
  `show.mock` ve el mensaje; `override(Notifications, ...)` reemplaza el cliente para cada subscriber que
  lo recibe.
- **Un proceso sin app de FastStream**, por ejemplo un `@job` que envía mensajes, es dueño de su conexión:
  ahí el broker se crea en el `connect()` del cliente y se detiene en su `disconnect()`, como `Nats` en
  [examples/faststream_nats/publish.py](../../../examples/faststream_nats/publish.py).
- **Varios brokers**, `FastStream(first, second)`, funcionan igual: `setup(app)` rellena los subscribers de
  cada uno, y un cliente recibe como valor por defecto el broker al que publica.

## <a id="one-app-at-a-time-in-tests"></a>Una app a la vez, en las pruebas

FastStream vuelve a construir un subscriber en cada arranque, así que nuke-di reescribe una función
subscriber una sola vez, sea cual sea el contenedor (`per_container=False` en
[Escribir una integración](integrations.md)), y cada app que arranca la rellena desde su propio contenedor.
Por eso, las pruebas que arrancan apps una tras otra pueden darle a cada una un contenedor propio, sobre el
broker a nivel de módulo:

```python
# tests/test_containers.py
from faststream import FastStream, TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.notify import broker, show
from nuke_di import Dependencies
from nuke_di.faststream import setup


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def make_app(container: Dependencies) -> FastStream:
    # A new app on the module-level broker, whose subscribers are declared on import
    app = FastStream(broker)
    setup(app, container)
    return app


async def test_greet(di: Dependencies) -> None:
    with di.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(make_app(di)):
            await test_broker.publish(1, "greetings")

            show.mock.assert_called_once_with("Hello, alice!")
```

```console
$ pytest -q tests/test_containers.py
.                                                                        [100%]
1 passed in 0.15s
```

Qué hacer al respecto:

- **Ejecuta una tras otra las pruebas que arrancan una app**, que es lo que hace pytest. pytest-xdist las
  ejecuta en procesos propios, que no comparten nada.
- **No arranques a la vez dos apps sobre las mismas funciones subscriber**, por ejemplo un `TestApp` dentro
  de otro. La segunda no logra arrancar y lanza `RuntimeError: nuke-di clients
  failed to start: UserService is filled for another app that is running; apps that share a handler
  function run one at a time`, en lugar de pasarle a la segunda los clientes de la primera.
- **`DI.override()` sobre la app a nivel de módulo o un contenedor por prueba**: ambos sirven; elige según lo
  que use el resto de las pruebas.
- A diferencia de esto, FastAPI reescribe una función para cada contenedor, y sus apps sobre contenedores
  distintos pueden correr a la vez; consulta [FastAPI](fastapi.md#an-app-per-test-container) para la única
  excepción.
