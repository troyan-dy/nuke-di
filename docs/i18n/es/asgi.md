# <a id="starlette-quart-and-any-asgi-app"></a>Starlette, Quart y cualquier app ASGI

[English](../../guide/asgi.md) · [Русский](../ru/asgi.md) · [简体中文](../zh-CN/asgi.md) · **Español** · [Português (Brasil)](../pt-BR/asgi.md) · [日本語](../ja/asgi.md) · [Polski](../pl/asgi.md)

← [Documentación](../README.es.md#documentation)

Un framework sin inyección de dependencias, como Starlette o Quart, no puede rellenar los argumentos de un
handler por su type hint. `nuke_di.asgi.lifespan()` cubre lo demás: es el lifespan de la app, que conecta
los clientes que enumera cuando la app arranca y los desconecta cuando se detiene, y un handler le pide un
cliente con `get()`. No importa ningún framework ni necesita extras:

```bash
pip install nuke-di
```

- [Starlette](#starlette)
- [El lifespan propio de la app](#the-apps-own-lifespan)
- [Pruebas](#testing)
- [Quart](#quart)
- [aiohttp](#aiohttp)
- [Una app ASGI sin framework](#a-plain-asgi-app)
- [Errores](#errors)

## <a id="starlette"></a>Starlette

Con los clientes de los ejemplos de [FastAPI](fastapi.md):

```python
# app/starlette_api.py
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.clients import Database, UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

# The clients the handlers take: they connect, with their dependencies, when the app starts
clients = lifespan(DI, UserService, Database)


async def get_user(request: Request) -> PlainTextResponse:
    users = clients.get(UserService)
    return PlainTextResponse(await users.greet(request.path_params["user_id"]))


async def me(request: Request) -> PlainTextResponse:
    db = clients.get(Database)
    return PlainTextResponse(await db.fetch_user(int(request.headers["X-User-Id"])))


app = Starlette(
    routes=[Route("/users/{user_id:int}", get_user), Route("/me", me)],
    lifespan=clients,
)
```

```console
$ uvicorn app.starlette_api:app
INFO:     Started server process [47010]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:50476 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:50478 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [47010]
```

```console
$ curl localhost:8000/users/42
Hello, user-42!
$ curl localhost:8000/me -H "X-User-Id: 7"
user-7
```

Las reglas:

- **La lista es explícita.** No hay una tabla de rutas en la que buscar los clientes, así que
  `lifespan(container, *clients)` nombra cada cliente que recibe un handler. `clients.get(Database)` solo
  funciona cuando `Database` está en la lista, aunque se conecte de todos modos como dependencia de
  `UserService`: un handler que contara con eso se rompería el día en que `UserService` dejara de depender de él.
- **`get()` está tipado.** `clients.get(UserService)` devuelve un `UserService` para mypy y pyright, a
  diferencia de un atributo de `request.state`. Es un método normal, así que funciona en un handler, un
  endpoint websocket, un middleware o una tarea en segundo plano, y los handlers importan el módulo que
  contiene `clients` como cualquier otro.
- **Se resuelve al arrancar.** Crear `clients` no resuelve nada; cada arranque vuelve a resolver los
  clientes de la lista, así que un `override()` antes de que arranque la app reemplaza uno, y un segundo
  arranque obtiene clientes nuevos.
- **Apagado.** Al apagarse se activa `Shutdown`, se detienen las `BackgroundTasks` y después los clientes
  se desconectan, en el mismo orden que en un [worker](workers-and-jobs.md).
- **Una app a la vez.** Un contenedor se conecta una sola vez: una segunda app arrancada sobre el mismo
  contenedor mientras la primera está en marcha falla en su arranque.
- **También FastAPI.** Una app de FastAPI que mantiene las firmas de sus rutas tal como están escritas pasa
  `FastAPI(lifespan=clients)` de la misma manera; con `nuke_di.fastapi.setup()` sus rutas reciben, en
  cambio, los clientes por type hint, ver [FastAPI](fastapi.md).

## <a id="the-apps-own-lifespan"></a>El lifespan propio de la app

`clients(app)` es un context manager asíncrono, así que el lifespan propio de la app entra primero en él y
ejecuta dentro su código de arranque y de apagado, con los clientes conectados. Lo que entrega con `yield`
es el estado de la app, como de costumbre:

```python
# app/own_lifespan.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.clients import UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService)


@asynccontextmanager
async def app_lifespan(app: Starlette) -> AsyncIterator[dict[str, str]]:
    async with clients(app):
        # The app's own startup and shutdown code runs with the clients connected
        greeting = await clients.get(UserService).greet(0)
        print("warmed up:", greeting)
        yield {"greeting": greeting}
        print("app: stopping")


async def index(request: Request) -> PlainTextResponse:
    return PlainTextResponse(request.state.greeting)


app = Starlette(routes=[Route("/", index)], lifespan=app_lifespan)
```

```console
$ uvicorn app.own_lifespan:app
INFO:     Started server process [46993]
INFO:     Waiting for application startup.
database: connected
warmed up: Hello, user-0!
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:50465 - "GET / HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
app: stopping
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [46993]
```

## <a id="testing"></a>Pruebas

Una prueba reemplaza un cliente antes de que `TestClient` arranque la app:

```python
# tests/test_starlette_api.py
from starlette.testclient import TestClient

from app.clients import Database
from app.starlette_api import app
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
$ pytest -q tests/test_starlette_api.py
.                                                                        [100%]
1 passed in 0.04s
```

`TestClient(app)` sin `with` envía peticiones sin ejecutar el lifespan, y el handler lo avisa:

```console
$ python -c "from starlette.testclient import TestClient; from app.starlette_api import app; TestClient(app).get('/users/1')"
Traceback (most recent call last):
  ...
RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` in Starlette or `async with app.test_app()` in Quart
```

## <a id="quart"></a>Quart

Quart no tiene un argumento `lifespan=`: ejecuta los hooks `before_serving` al arrancar y los hooks
`after_serving` al apagarse, cada tipo en el orden en que se registró, y se salta el resto cuando uno falla.
Una subclase de `Quart` conecta los clientes alrededor de todos ellos, así que cualquier hook de la app puede
usar los clientes, y un hook que falla los deja igualmente desconectados:

```python
# app/quart_api.py
from contextlib import AsyncExitStack

from quart import Quart, request

from app.clients import Database, UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService, Database)
running = AsyncExitStack()


class ClientsQuart(Quart):
    """
    Connects the clients before every before_serving hook and disconnects them after every after_serving
    hook, also when one of the hooks fails.
    """

    async def startup(self) -> None:
        await running.enter_async_context(clients(self))
        try:
            await super().startup()
        except BaseException:
            await running.aclose()
            raise

    async def shutdown(self) -> None:
        try:
            await super().shutdown()
        finally:
            await running.aclose()


app = ClientsQuart(__name__)


@app.before_serving
async def warm_up() -> None:
    print("warm-up:", await clients.get(UserService).greet(0))


@app.after_serving
async def goodbye() -> None:
    print("goodbye:", await clients.get(UserService).greet(1))


@app.get("/users/<int:user_id>")
async def get_user(user_id: int) -> str:
    return await clients.get(UserService).greet(user_id)


@app.get("/me")
async def me() -> str:
    return await clients.get(Database).fetch_user(int(request.headers["X-User-Id"]))
```

```console
$ uvicorn app.quart_api:app
INFO:     Started server process [72984]
INFO:     Waiting for application startup.
database: connected
warm-up: Hello, user-0!
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:57604 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:57606 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
goodbye: Hello, user-1!
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [72984]
```

Una prueba arranca la app con `test_app()`:

```python
# tests/test_quart_api.py
from app.clients import Database
from app.quart_api import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_get_user() -> None:
    with DI.override(Database, FakeDatabase()):
        async with app.test_app() as test_app:
            response = await test_app.test_client().get("/users/1")
            assert await response.get_data(as_text=True) == "Hello, alice!"
```

```console
$ pytest -q tests/test_quart_api.py
.                                                                        [100%]
1 passed in 0.13s
```

¿Por qué no dos hooks de la app, uno que conecta y otro que desconecta? Cada uno se ejecutaría en su turno
entre los hooks propios de la app. Un `disconnect` en `after_serving` se ejecuta antes que los hooks
`after_serving` registrados después de él, que encuentran entonces los clientes desconectados; un hook
`before_serving` que falla después del que conectó deja el contenedor conectado, porque Quart ya no llama a
ningún hook `after_serving`, y el siguiente arranque falla con `the container is already connected`.
`Quart.startup()` y `Quart.shutdown()` ejecutan todos los hooks, así que la subclase conecta antes del primero
y desconecta después del último, falle el que falle. El `while_serving` de Quart quedaría más corto, pero crea
su generador una sola vez, al registrarse, así que la app solo podría arrancar una vez por proceso, y la
segunda prueba que la arranca fallaría.

## <a id="aiohttp"></a>aiohttp

aiohttp 3.14 acepta un context manager asíncrono en `cleanup_ctx`, y `clients` lo es:

```python
# app/aiohttp_api.py
from aiohttp import web

from app.clients import Database, UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService, Database)


async def get_user(request: web.Request) -> web.Response:
    users = clients.get(UserService)
    return web.Response(text=await users.greet(int(request.match_info["user_id"])))


app = web.Application()
app.router.add_get("/users/{user_id}", get_user)
app.cleanup_ctx.append(clients)  # aiohttp 3.14 or newer

if __name__ == "__main__":
    web.run_app(app)
```

```console
$ python -m app.aiohttp_api
database: connected
======== Running on http://0.0.0.0:8080 ========
(Press CTRL+C to quit)
^C
database: disconnected
```

```console
$ curl localhost:8080/users/42
Hello, user-42!
```

Un aiohttp más antiguo acepta en su lugar un generador asíncrono:

```python
from collections.abc import AsyncIterator


async def run_clients(app: web.Application) -> AsyncIterator[None]:
    async with clients(app):
        yield


app.cleanup_ctx.append(run_clients)
```

## <a id="a-plain-asgi-app"></a>Una app ASGI sin framework

Una app ASGI sin framework responde ella misma a los mensajes de lifespan del servidor. Ahí `clients()` no
recibe argumentos:

```python
# app/raw_asgi.py
from typing import Any

from app.clients import UserService
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService)


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        await receive()  # lifespan.startup
        started = False
        try:
            async with clients():
                await send({"type": "lifespan.startup.complete"})
                started = True
                await receive()  # lifespan.shutdown
        except Exception as exc:
            await send({"type": f"lifespan.{'shutdown' if started else 'startup'}.failed", "message": str(exc)})
            raise
        await send({"type": "lifespan.shutdown.complete"})
        return

    body = (await clients.get(UserService).greet(42)).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})
```

```console
$ uvicorn app.raw_asgi:app
INFO:     Started server process [48646]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:50998 - "GET / HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [48646]
```

## <a id="errors"></a>Errores

| Cuándo | Qué se lanza |
|---|---|
| Un handler se ejecuta sin el lifespan de la app, o después de que se detuvo | ``RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` in Starlette or `async with app.test_app()` in Quart `` |
| `get()` de un cliente que no está en la lista | ``RuntimeError: Database is not a client of this lifespan: list it in `lifespan(container, ...)` `` |
| El `connect()` de un cliente falla | `RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused`; el servidor informa de un arranque fallido y termina, y el contenedor queda vaciado con `flush()` |
| El contenedor ya está conectado, por ejemplo por otra app | `RuntimeError: nuke-di clients failed to start: the container is already connected` |
| `lifespan(Database)`, sin el contenedor | `TypeError: lifespan() takes the container first, then the clients, e.g. lifespan(DI, Database); got <class 'app.clients.Database'>` |
| `lifespan(DI, "Database")`, que no es una clase de cliente | `TypeError: 'Database' is not a client: subclass Client or NotSingletonClient` |

Bajo uvicorn, con un `Database` cuyo `connect()` lanza `OSError("connection refused")`:

```console
$ uvicorn app.broken_api:app
INFO:     Started server process [44319]
INFO:     Waiting for application startup.
Database.connect() raised OSError: connection refused
...
RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused

ERROR:    Application startup failed. Exiting.
```
