# <a id="writing-an-integration"></a>Escribir una integración

[English](../../guide/integrations.md) · [Русский](../ru/integrations.md) · [简体中文](../zh-CN/integrations.md) · **Español** · [Português (Brasil)](../pt-BR/integrations.md) · [日本語](../ja/integrations.md) · [Polski](../pl/integrations.md)

← [Documentación](../README.es.md#documentation)

Una integración con un framework hace dos cosas: los handlers del framework reciben clientes por su type hint,
a través de la inyección de dependencias propia del framework, y el contenedor se conecta cuando la app arranca
y se desconecta cuando se detiene. Las integraciones de [FastAPI](fastapi.md), [Litestar](litestar.md) y
[FastStream](faststream.md) están construidas sobre `nuke_di.integration`, y una integración con otro
framework no necesita de nuke-di nada más que eso y la API pública.
Un servidor sin inyección de dependencias propia, como grpc.aio, aiohttp, websockets, APScheduler, Textual o un
worker de Temporal, no necesita ninguna integración: se ejecuta dentro de un `@worker`, consulta
[Servidores dentro de un worker](servers-in-workers.md).

| Nombre | Qué hace |
|---|---|
| `Framework(name, not_started, not_connected)` | Lo que se les dice a los usuarios de un framework cuando falta un cliente; `{client}` en un mensaje es el nombre de la clase del cliente. |
| `DependsFramework(..., depends, make_depends, per_container=True)` | Un framework que inyecta mediante marcadores `Depends(...)`: `depends` es la clase de sus marcadores, `make_depends` construye uno para una función. |
| `bind(call, container, framework)` | Reescribe la firma de un handler, de una función de dependencia o de una clase de dependencia, y de las dependencias que usa: cada argumento de tipo cliente pasa a ser `Annotated[Client, Depends(...)]`. Devuelve el `Binding` de cada cliente. |
| `Binding` | Un argumento de tipo cliente; `get()` devuelve el cliente resuelto al arrancar, o lanza `not_started` / `not_connected`. |
| `running(container, bindings)` | Un context manager asíncrono: resuelve los clientes de `bindings`, conecta el contenedor y, al salir, activa `Shutdown`, detiene las `BackgroundTasks` y desconecta. Un `ConnectError` o un `InitializeDependencyError` se convierte en un `RuntimeError`, que un servidor reporta como un arranque fallido; un error del árbol de clientes, como un ciclo, pasa tal cual. |
| `wrap_lifespan(original, container, bindings)` | Un lifespan que ejecuta el lifespan `original` propio de la app dentro de `running()`; `bindings` se llama al arrancar, así que se encuentran los handlers declarados después de `setup()`. |
| `client_of(hint, *markers)` | El cliente que pide un type hint, o `None`: `Client`, o `Annotated[Client, ...]` sin ninguno de los `markers`. |
| `unique(bindings)` | `bindings` sin repeticiones: a menudo una misma dependencia es alcanzable desde varios handlers. |

## <a id="a-framework-with-depends"></a>Un framework con `Depends`

FastAPI y FastStream (a través de fast-depends) leen `inspect.signature()` de un handler y llaman a la dependencia
de cada marcador `Depends(...)`, y lo mismo hace cualquier framework cuyos marcadores funcionen igual. Para ellos, `bind()` sustituye `users: UserService` por
`users: Annotated[UserService, Depends(binding.get)]`, y el framework hace el resto. La integración de
FastStream, escrita solo con el kit público, es este módulo:

```python
# myapp/faststream_di.py
from typing import Any

from faststream import Depends, FastStream

from nuke_di import DI, Dependencies
from nuke_di.integration import Binding, DependsFramework, bind, unique, wrap_lifespan


def _noop() -> None: ...


FRAMEWORK = DependsFramework(
    name="FastStream",
    # The class of FastStream's markers, and the function that builds one
    depends=type(Depends(_noop)),
    make_depends=Depends,
    not_started="{client} was not started with the app: declare its subscriber before the app starts",
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`",
    # FastStream builds a subscriber on every start: a function is bound once, whatever the container
    per_container=False,
)


def setup(app: FastStream, container: Dependencies = DI) -> None:
    # Connect the container around the app's own lifespan, with the clients found on startup
    app.lifespan_context = wrap_lifespan(app.lifespan_context, container, lambda: _bindings(app, container))
    for broker in app.brokers:
        # Rewrite a subscriber's function whenever FastStream builds the subscriber
        config = broker.config.fd_config
        config.call_decorators = (*config.call_decorators, _Rewrite(container))


class _Rewrite:
    def __init__(self, container: Dependencies) -> None:
        self.container = container

    def __call__(self, call: Any) -> Any:
        bind(call, self.container, FRAMEWORK)
        return call


def _bindings(app: FastStream, container: Dependencies) -> list[Binding]:
    # The clients of every subscriber and of the dependencies it declares, each once
    bindings: list[Binding] = []
    for broker in app.brokers:
        for subscriber in broker.subscribers:
            for item in subscriber.calls:
                bindings += bind(item.handler._declared_call, container, FRAMEWORK)
                for depends in item.dependencies:
                    bindings += bind(depends.dependency, container, FRAMEWORK)
    return unique(bindings)
```
Hay tres cosas específicas de cada framework, y cada integración las encuentra en los detalles internos de ese framework:

- **Cuándo se lee la firma.** `bind()` debe ejecutarse antes de que el framework lea la firma del handler.
  FastAPI la lee cuando se declara una ruta, así que `nuke_di.fastapi` hace el bind en su clase de ruta; FastStream
  la lee cuando construye un subscriber, en cada arranque, así que el módulo de arriba hace el bind en un hook `call_decorators`.
- **Dónde están los handlers.** Al arrancar, `wrap_lifespan()` llama a `bindings()` para saber qué clientes
  resolver: la integración recorre las rutas, los subscribers o las tareas de la app, hace el bind de cada uno y devuelve los
  `Binding`. Un cliente al que nadie llega desde la app no se conecta.
- **Dónde está el lifespan.** `wrap_lifespan()` sustituye el lifespan de la app; el lifespan propio de la app se ejecuta
  dentro de él, así que su código de arranque puede usar los clientes.

El `nuke_di.faststream` real añade lo que el ejemplo omite: FastStream 0.6, las dependencias de los brokers
y de los routers, y un hook de reescritura por broker cuando varias apps lo comparten.

### <a id="per-container"></a>`per_container`

`bind()` reescribe una función en su sitio y recuerda el contenedor al que se vinculó. Con `per_container=True`
(el valor por defecto, FastAPI), una función vinculada a un contenedor y declarada de nuevo para otro se vincula de nuevo: FastAPI
lee la firma de una ruta una sola vez, cuando se declara la ruta, así que cada app conserva los bindings que capturó, y dos
apps sobre dos contenedores pueden servir la misma función a la vez.

Con `per_container=False` (FastStream), una función se vincula una sola vez, sea cual sea el contenedor, y cada app que
arranca resuelve los mismos `Binding`. FastStream construye un subscriber en cada arranque, y con un broker de pruebas
incluso antes de que se ejecute el lifespan de la app, así que la firma no debe cambiar de una app a otra. El coste: dos
apps que comparten una función handler se ejecutan una tras otra, y la segunda lanza `RuntimeError: ... is filled for
another app that is running`. Elige `False` cuando el framework pueda volver a leer una firma después de que la primera
app haya arrancado.

## <a id="a-framework-without-depends"></a>Un framework sin `Depends`

Litestar proporciona las dependencias por nombre, y aiogram las pasa por nombre desde un middleware. Ahí `bind()` no
aplica: usa `Framework` para los mensajes, `client_of()` para encontrar los argumentos de tipo cliente de un handler, un
`Binding` por cliente cuyo `get` el framework llama por sus propios medios, y `running()` dentro del lifespan
de la app. `nuke_di.litestar` es el ejemplo completo: registra `Provide(binding.get)` bajo el nombre
del argumento.

## <a id="checking-an-integration"></a>Comprobar una integración

`nuke_di.integration.testing.check()` ejecuta contra tu integración el contrato que cumple toda integración:

- un handler recibe un cliente por su type hint, conectado mientras la app se ejecuta;
- una dependencia de un handler recibe un cliente por su type hint (solo con un `DependsFramework`);
- `override()` antes del arranque sustituye un cliente que el handler recibe a través de otro cliente;
- un handler llamado sin el lifespan de la app lanza el error `not_connected` del framework;
- un `connect()` fallido hace fallar el arranque de la app con un `RuntimeError` y deja el contenedor vaciado.

Trae sus propios clientes y handlers. Tú le das el framework y dos funciones: `make_app(container,
handler)` devuelve una app nueva configurada con `container` que sirve `handler`, una `async def` sin más argumentos
que clientes; `run(app, lifespan)` es un context manager asíncrono que ejecuta la app, con su lifespan solo cuando
`lifespan` es verdadero, y produce (yield) un `send()` que llama al handler una vez a través del framework y lanza lo que
lanzó el handler. Para el módulo de arriba:

```python
# tests/test_contract.py
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from faststream import FastStream, TestApp
from faststream.nats import NatsBroker, TestNatsBroker

from myapp.faststream_di import FRAMEWORK, setup
from nuke_di import Dependencies
from nuke_di.integration.testing import Send, check


def make_app(container: Dependencies, handler: Callable[..., Any]) -> FastStream:
    broker = NatsBroker()
    app = FastStream(broker)
    setup(app, container)
    broker.subscriber("check")(handler)
    return app


@asynccontextmanager
async def run(app: FastStream, lifespan: bool) -> AsyncIterator[Send]:
    # connect_only: FastStream would guess it from the mention of TestApp below
    async with TestNatsBroker(app.broker, connect_only=lifespan) as broker:
        if lifespan:
            async with TestApp(app):
                yield lambda: broker.publish(None, "check")
        else:
            yield lambda: broker.publish(None, "check")


async def test_contract() -> None:
    await check(FRAMEWORK, make_app, run)
```
```console
$ pytest -q tests/test_contract.py
.                                                                        [100%]
1 passed in 0.29s
```
`check()` es una corrutina: ejecútala con pytest-asyncio o anyio. Lanza un `ExceptionGroup` con cada caso
que falló, cada uno con una nota que nombra el caso. Si se omite la línea `wrap_lifespan()` de `setup()`, el
contenedor nunca se conecta:

```console
$ pytest -q --tb=short tests/test_contract.py
F                                                                        [100%]
  | ExceptionGroup: the FastStream integration breaks the nuke-di contract (4 sub-exceptions)
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case handler: a handler takes a client by type hint, connected for the time the app runs
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case dependency: a dependency of a handler takes a client by type hint
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case override: override() before startup replaces a client the handler gets through another client
    | AssertionError: expected a RuntimeError with 'nuke-di clients failed to start' about Broken, got RuntimeError('Broken is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`')
    | FastStream integration, case failed_connect: a failed connect() fails the app's startup with a RuntimeError and leaves the container flushed
FAILED tests/test_contract.py::test_contract - ExceptionGroup: the FastStream...
1 failed in 0.17s
```
(Tracebacks abreviados.) Un framework que reporta el error de un handler en lugar de lanzarlo necesita un `send()`
que lo lance: el cliente de pruebas de un framework HTTP devuelve un 500, así que `send()` comprueba el código de estado. Litestar pone
el error en la respuesta solo con `debug=True`.

`nuke_di._integration`, el módulo privado en el que vivía este kit antes de 1.14.0, todavía se importa con un
`DeprecationWarning` y se elimina en 1.15.0.
