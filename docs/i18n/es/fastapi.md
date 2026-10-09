# <a id="fastapi"></a>FastAPI

[English](../../guide/fastapi.md) · [Русский](../ru/fastapi.md) · [简体中文](../zh-CN/fastapi.md) · **Español** · [Português (Brasil)](../pt-BR/fastapi.md) · [日本語](../ja/fastapi.md) · [Polski](../pl/fastapi.md)

← [Documentación](../README.es.md#documentation)

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
   los routers que incluye, y los conectó, cada uno después de sus dependencias. Al apagarse, los
   desconectó.
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
`TestClient` arranque la app, con [`override()`](testing.md) o con el fixture `global_di`:

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
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
ERROR:    Traceback (most recent call last):
  ...
nuke_di.errors.ConnectError: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ...
RuntimeError: nuke-di clients failed to start: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable

ERROR:    Application startup failed. Exiting.
$ echo $?
3
```

## <a id="not-supported"></a>No admitido

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
