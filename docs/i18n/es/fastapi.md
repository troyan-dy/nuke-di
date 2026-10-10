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
- **Un contenedor por app.** Cada app recibe los clientes del contenedor pasado a su `setup()`, así que dos
  apps sobre dos contenedores sirven las mismas funciones a la vez, por ejemplo en las pruebas. Una app montada
  en una app con `setup()`, o una que sirve sus rutas, por ejemplo mediante `include_router(api.router)`, que
  ejecuta el lifespan de `api`, recibe los clientes que arrancó esa app.
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

## <a id="class-based-views"></a>Vistas basadas en clases

Las rutas que comparten lo que necesitan, el usuario de la petición y algunos clientes, lo reciben como
una sola clase. La clase es una dependencia de FastAPI con un `__init__` con type hints, escrita igual
que un cliente, y su `__init__` recibe datos de la petición y clientes uno al lado del otro:

```python
# app/views.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di.fastapi import setup

app = FastAPI()
setup(app)


class Account:
    # Built by FastAPI for every request, from a header and two clients
    def __init__(self, x_user_id: Annotated[int, Header()], db: Database, users: UserService) -> None:
        self.user_id = x_user_id
        self.db = db
        self.users = users

    async def name(self) -> str:
        return await self.db.fetch_user(self.user_id)

    async def greeting(self) -> str:
        return await self.users.greet(self.user_id)


CurrentAccount = Annotated[Account, Depends()]


@app.get("/me")
async def me(account: CurrentAccount) -> str:
    return await account.name()


@app.get("/me/greeting")
async def greeting(account: CurrentAccount) -> str:
    return await account.greeting()
```

```console
$ uvicorn app.views:app
INFO:     Started server process [50908]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51798 - "GET /me HTTP/1.1" 200 OK
INFO:     127.0.0.1:51800 - "GET /me/greeting HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [50908]
```

```console
$ curl localhost:8000/me -H "X-User-Id: 7"
"user-7"
$ curl localhost:8000/me/greeting -H "X-User-Id: 7"
"Hello, user-7!"
```

Una prueba reemplaza un cliente una sola vez para todas las rutas que usan la clase:

```python
# tests/test_views.py
from fastapi.testclient import TestClient

from app.clients import Database
from app.views import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_account() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/me", headers={"X-User-Id": "7"}).json() == "alice"
        assert client.get("/me/greeting", headers={"X-User-Id": "7"}).json() == "Hello, alice!"
```

```console
$ pytest -q tests/test_views.py
.                                                                        [100%]
1 passed in 0.18s
```

Las reglas:

- **Una vista por petición, un cliente por contenedor.** FastAPI construye un `Account` en cada petición;
  el `db` y el `users` que contiene son los clientes conectados del contenedor, los mismos objetos en
  todas las peticiones. nuke-di reescribió la firma de la clase, como hace con una función de dependencia,
  y dejó intacto su `__init__`: `Account(x_user_id=7, db=db, users=users)` en una prueba unitaria funciona
  como siempre. Una dataclass funciona igual, y sus campos son los argumentos.
- **Una vista que no necesita nada de la petición es un cliente.** `class Account(Client)` recibido como
  `account: Account` se construye una vez por contenedor y se conecta junto con los demás. Una clase de
  cliente escrita como `Annotated[Account, Depends()]`, por ejemplo una decorada con `@client_dataclass`,
  la construye FastAPI en cada petición, y su `connect()` nunca se ejecuta: omite el `Depends()`.
- **El `@cbv` de fastapi-utils no hace falta.** Vuelve a declarar las rutas en un `APIRouter` común
  propio, así que un atributo de clase tipado como cliente falla en `include_router()` con
  `TypeError: Database is a nuke-di client, not a pydantic type`. La clase de arriba comparte los clientes
  entre rutas sin nada más que FastAPI.

## <a id="an-app-per-test-container"></a>Una app por contenedor de prueba

Una fábrica de apps construye la app alrededor del contenedor que recibe, así que cada prueba corre sobre
un contenedor propio y el servidor sobre el `DI` global. Las funciones se quedan a nivel de módulo:

```python
# app/factory.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di import DI, Dependencies
from nuke_di.fastapi import ClientRouter, setup


async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user


def make_app(container: Dependencies) -> FastAPI:
    app = FastAPI()
    setup(app, container)
    app.add_api_route("/users/{user_id}", get_user)

    # A router fills clients from one container, so every app creates its own
    account = ClientRouter(prefix="/me", container=container)
    account.add_api_route("", me)
    app.include_router(account)
    return app


def create_app() -> FastAPI:
    # For the server: `uvicorn --factory app.factory:create_app`
    return make_app(DI)
```

```console
$ uvicorn --factory app.factory:create_app
INFO:     Started server process [50968]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51815 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51817 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [50968]
```

Las pruebas construyen una app sobre el fixture `di`:

```python
# tests/test_factory.py
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.clients import Database
from app.factory import current_user, make_app
from nuke_di import Dependencies


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


@pytest.fixture
def app(di: Dependencies) -> Iterator[FastAPI]:
    # `di` is a fresh container for every test, a fixture of nuke-di
    with di.override(Database, FakeDatabase()):
        yield make_app(di)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_get_user(client: TestClient) -> None:
    assert client.get("/users/1").json() == "Hello, alice!"


def test_me(client: TestClient) -> None:
    assert client.get("/me", headers={"X-User-Id": "7"}).json() == "alice"


def test_dependency_overrides(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[current_user] = lambda: "carol"
    assert client.get("/me").json() == "carol"
```

```console
$ pytest -q tests/test_factory.py
...                                                                      [100%]
3 passed in 0.16s
```

Las reglas:

- **Un solo `override()` para toda la app.** `di.override(Database, FakeDatabase())` reemplaza la base de
  datos para la dependencia `current_user`, para `UserService` y para todo lo demás que recibe un
  `Database`, mientras que FastAPI por sí solo necesita una entrada de `app.dependency_overrides` por cada
  función de dependencia.
- **Una función sobre varios contenedores.** `get_user` y `current_user` se reescriben una sola vez; cada
  app resuelve sus clientes en su propio contenedor al arrancar, y una petición recibe los clientes de la
  app a la que llegó. Apps construidas sobre contenedores distintos sirven las mismas funciones, una tras
  otra o a la vez.
- **Un router por app.** Un `ClientRouter` rellena clientes desde un solo contenedor; una app que incluye un
  router de otro contenedor lanza `TypeError: the router fills clients from another container than
  this app`. Crea los routers dentro de la fábrica.
- **`app.dependency_overrides`** pertenece a una sola app y sigue funcionando, también para una dependencia
  que recibe clientes, como `current_user` arriba.

## <a id="strawberry-graphql"></a>Strawberry GraphQL

El router de Strawberry para FastAPI, `GraphQLRouter`, es un `APIRouter`, y sus rutas construyen el
contexto de GraphQL con una dependencia de FastAPI. El contexto es entonces una clase con un `__init__`
con type hints que recibe los clientes, y los resolvers los leen de `info.context`:

```bash
pip install "nuke-di[fastapi]" strawberry-graphql
```

```python
# app/graphql.py
from collections.abc import AsyncIterator

import strawberry
from fastapi import FastAPI
from strawberry.fastapi import BaseContext, GraphQLRouter

from app.clients import UserService
from nuke_di.fastapi import ClientRoute, setup


class Context(BaseContext):
    # Built by FastAPI for every request, as a dependency of Strawberry's routes
    def __init__(self, users: UserService) -> None:
        super().__init__()
        self.users = users


@strawberry.type
class Query:
    @strawberry.field
    async def greeting(self, info: strawberry.Info[Context], user_id: int) -> str:
        return await info.context.users.greet(user_id)


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def greetings(self, info: strawberry.Info[Context], user_ids: list[int]) -> AsyncIterator[str]:
        for user_id in user_ids:
            yield await info.context.users.greet(user_id)


schema = strawberry.Schema(query=Query, subscription=Subscription)

app = FastAPI()
setup(app)
graphql = GraphQLRouter(schema, context_getter=Context, route_class=ClientRoute)
app.include_router(graphql, prefix="/graphql")
```

```console
$ uvicorn app.graphql:app
INFO:     Started server process [61129]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51979 - "POST /graphql HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [61129]
```

```console
$ curl localhost:8000/graphql -H 'Content-Type: application/json' -d '{"query": "{ greeting(userId: 42) }"}'
{"data":{"greeting":"Hello, user-42!"}}
```

Una consulta, y una suscripción por el websocket del mismo router:

```python
# tests/test_graphql.py
from fastapi.testclient import TestClient

from app.clients import Database
from app.graphql import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_query() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        response = client.post("/graphql", json={"query": "{ greeting(userId: 1) }"})

    assert response.json() == {"data": {"greeting": "Hello, alice!"}}


def test_subscription() -> None:
    query = "subscription { greetings(userIds: [1, 2]) }"
    with (
        TestClient(app) as client,
        client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as ws,
    ):
        ws.send_json({"type": "connection_init"})
        assert ws.receive_json() == {"type": "connection_ack"}
        ws.send_json({"id": "1", "type": "subscribe", "payload": {"query": query}})
        assert ws.receive_json()["payload"] == {"data": {"greetings": "Hello, user-1!"}}
        assert ws.receive_json()["payload"] == {"data": {"greetings": "Hello, user-2!"}}
        assert ws.receive_json() == {"id": "1", "type": "complete"}
```

```console
$ pytest -q tests/test_graphql.py
..                                                                       [100%]
2 passed in 0.21s
```

Las reglas:

- **El contexto recibe los clientes, los resolvers reciben el contexto.** FastAPI construye un `Context`
  en cada petición y en cada conexión websocket, con los clientes conectados del contenedor dentro;
  `strawberry.Info[Context]` les da a los resolvers su tipo. Un resolver no recibe ningún cliente por type
  hint: Strawberry no tiene inyección de dependencias propia, e `info.context` es su forma de pasarles las
  cosas.
- **`route_class=ClientRoute`** hace que las rutas del router rellenen clientes, como en cualquier
  `APIRouter`. Strawberry le pasa el context getter a FastAPI envuelto en una dependencia propia, y nuke-di
  la sigue hasta `Context`.
- **Suscripciones.** FastAPI construye la ruta websocket del router sin la route class, como cualquier
  websocket de un `APIRouter`, y aun así recibe un `Context` con los clientes: comparte la dependencia de
  contexto de Strawberry con las rutas GET y POST, que el router declara antes a través de `ClientRoute`.
  Estas reescriben `Context` y llevan sus clientes al arranque de la app. Un endpoint websocket propio en
  un router así sigue lanzando `TypeError`, consulta [No admitido](#not-supported).
- **Otro contenedor**: `route_class=app.router.route_class`, después de `setup(app, container)`.
- El controller de Strawberry para Litestar recibe clientes a través de `ClientPlugin`, consulta
  [Litestar](litestar.md#strawberry-graphql).
