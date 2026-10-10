# <a id="litestar"></a>Litestar

[English](../../guide/litestar.md) · [Русский](../ru/litestar.md) · [简体中文](../zh-CN/litestar.md) · **Español** · [Português (Brasil)](../pt-BR/litestar.md) · [日本語](../ja/litestar.md) · [Polski](../pl/litestar.md)

← [Documentación](../README.es.md#documentation)

Un route handler de Litestar también recibe un cliente por su type hint, a través de un plugin:

```bash
pip install "nuke-di[litestar]"
```

Requiere Litestar 2.15 o posterior. Con los clientes de los ejemplos de [FastAPI](fastapi.md):

```python
# app/litestar_api.py
from typing import Annotated

from litestar import Litestar, get
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, HeaderParameter

from app.clients import Database, UserService
from nuke_di.litestar import ClientPlugin


@get("/users/{user_id:int}")
async def get_user(user_id: FromPath[int], users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, HeaderParameter(name="X-User-Id")], db: Database) -> str:
    return await db.fetch_user(x_user_id)


@get("/me", dependencies={"user": Provide(current_user)})
async def me(user: NamedDependency[str]) -> str:
    return user


app = Litestar([get_user, me], plugins=[ClientPlugin()])
```

```console
$ uvicorn app.litestar_api:app
INFO:     Started server process [6801]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51940 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51942 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [6801]
```

```console
$ curl localhost:8000/users/42
Hello, user-42!
$ curl localhost:8000/me -H "X-User-Id: 7"
user-7
```

`ClientPlugin()` encontró `users: UserService` en `get_user` y `db: Database` en la dependencia
`current_user`, se los proporcionó a Litestar como dependencias y los conectó al arrancar.

Las reglas:

- **Dónde se rellenan los clientes.** En los argumentos de los handlers HTTP y `@websocket` con los que se
  crea la app, incluidos los de routers y controllers a cualquier profundidad, y de cada dependencia
  declarada en la app, en un router, en un controller o en un handler: funciones y clases.
- **Por nombre.** Litestar proporciona las dependencias por nombre de argumento, así que nuke-di
  proporciona cada argumento de tipo cliente con su nombre, en la app. Un nombre equivale a un cliente
  en toda la app: `users: UserService` en un handler y `users: Billing` en otro lanzan `TypeError` al
  crear la app. Una dependencia con el mismo nombre declarada por la app, un router, un controller o
  un handler tiene prioridad sobre el cliente.
- **Instancias.** Un `Client` es una única instancia por contenedor; un `NotSingletonClient` es una
  instancia por nombre de argumento.
- **Lifespan.** Los clientes se conectan antes de que se ejecuten el `lifespan=` y los `on_startup=`
  propios de la app, y se desconectan después de sus hooks `on_shutdown=`, que Litestar llama al final.
  `Shutdown` y `BackgroundTasks` se comportan igual que en [FastAPI](fastapi.md).
- **La función sigue siendo una función.** Sus argumentos de tipo cliente ahora están anotados como
  dependencias explícitas de Litestar cuyo valor no se valida,
  `Annotated[UserService, Dependency(), SkipValidationMarker()]`, que es lo que pide Litestar 2.23 en
  lugar de una dependencia emparejada solo por nombre. Llamar a la función directamente funciona como
  siempre.
- **Plugins.** Pon `ClientPlugin()` después de cualquier plugin que añada route handlers: ve los handlers
  que tiene la app cuando le llega su turno.
- **Otro contenedor.** `ClientPlugin(container)`.

**Pruebas.** Igual que con FastAPI, una prueba reemplaza un cliente antes de que `TestClient` arranque la app:

```python
# tests/test_litestar_api.py
from litestar.testing import TestClient

from app.clients import Database
from app.litestar_api import app
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
$ pytest -q tests/test_litestar_api.py
.                                                                        [100%]
1 passed in 0.23s
```

**No admitido.** Un websocket listener, `@websocket_listener` o una clase `WebsocketListener`, no acepta
clientes: Litestar lee su firma en el momento de declararlo, antes de que el plugin lo vea, así que la
app lanza `TypeError` y sugiere en su lugar un handler `@websocket`. Un argumento de tipo cliente con un
nombre que Litestar reserva, como `state` o `request`, también lanza `TypeError`. Un handler registrado
después de crear la app, con `app.register()`, no se ve.

## <a id="differences-from-fastapi"></a>Diferencias con FastAPI

Un handler se escribe igual en ambos, `users: UserService`. Lo que cambia viene de cómo inyecta cada
framework: FastAPI lee un `Depends` en la firma de cada función, Litestar empareja una dependencia por el
nombre del argumento, así que `ClientPlugin` proporciona cada cliente con su nombre de argumento, en la
app ([ADR-0004](../../adr/0004-litestar-clients-by-name.md)).

| | FastAPI | Litestar |
|---|---|---|
| Configuración | `setup(app)` antes de las rutas; routers a través de `ClientRouter` | `ClientPlugin()` en `plugins=`; cualquier router o controller |
| Handlers que ve | Cada ruta declarada después de `setup(app)`, en la app o en un `ClientRouter` incluido | Los handlers con los que se crea la app; uno añadido después con `app.register()`, no |
| Nombres de argumento | Libres: `users: UserService` aquí y `users: Billing` allá | Un nombre, un cliente en toda la app; dos clientes con el mismo nombre lanzan `TypeError` al crear la app |
| Un `NotSingletonClient` | Una instancia por argumento | Una instancia por nombre de argumento, compartida por todos los handlers que usan ese nombre |
| Qué cambia en la función | Su `__signature__`; `get_type_hints()` sigue mostrando `UserService` | Sus `__annotations__`; `get_type_hints(include_extras=True)` muestra `Annotated[UserService, Dependency(), SkipValidationMarker()]` |
| Websockets | Endpoints `@app.websocket` | Handlers `@websocket`; un websocket listener lanza `TypeError` |
| El lifespan propio de la app | Se ejecuta por dentro: los clientes se conectan antes y se desconectan después | Los clientes se conectan antes de `lifespan=` y `on_startup=`, y se desconectan después de `on_shutdown=` |
| Reemplazar una dependencia en una prueba | `override()`, o `app.dependency_overrides` | `override()`, o una dependencia con el mismo nombre en una capa, que tiene prioridad sobre el cliente |
| Otro contenedor | `setup(app, container)` y `ClientRouter(container=container)` | `ClientPlugin(container)` |

## <a id="litestar-3"></a>Litestar 3

Litestar 3 todavía no se ha publicado: a 2026-10-10, la última versión en PyPI es la 2.24.0, del
2026-06-11, y no hay ninguna prerrelease de 3.0. Se saben dos cosas de ella, y nuke-di no cambia nada
hasta que salga:

- **Las dependencias inferidas desaparecen.** Litestar 2.24 avisa sobre una dependencia emparejada solo por
  su nombre: `Inferred dependencies will stop working in Litestar 3.0`. La anotación que escribe nuke-di,
  `Annotated[UserService, Dependency(), SkipValidationMarker()]`, es lo que significan `NamedDependency[...]`
  y `SkipValidation[...]`, la forma explícita, y Litestar 2.24 no avisa de nada de ello.
- **Se planea la inyección por tipo.** El [anuncio de v3](https://litestar.dev/blog/v3-announcement)
  del 2026-07-26 planea `TypeDependency[SomeService]` junto a `NamedDependency`, con el provider indexado
  por el tipo, `dependencies={SomeService: provide_some_service}`, y señala esta renovación del DI como la
  funcionalidad que todavía separa a 3.0 de su beta.

Con providers indexados por tipo, `ClientPlugin` podría proporcionar cada cliente con su clase en lugar de
con su nombre de argumento, y las filas de la tabla de arriba sobre nombres e instancias desaparecerían. Si
lo hará se decide cuando salgan 3.0 y su API, revisando ADR-0004. Los handlers no cambian en ningún caso:
declaran `users: UserService`, y solo el plugin decide cómo se le comunica eso a Litestar.

## <a id="strawberry-graphql"></a>Strawberry GraphQL

El controller de Strawberry para Litestar construye el contexto de GraphQL con una dependencia de
Litestar, así que un context getter recibe clientes como cualquier otra dependencia, y los resolvers los
leen de `info.context`:

```bash
pip install "nuke-di[litestar]" strawberry-graphql
```

```python
# app/litestar_graphql.py
import strawberry
from litestar import Litestar
from strawberry.litestar import BaseContext, make_graphql_controller

from app.clients import UserService
from nuke_di.litestar import ClientPlugin


class Context(BaseContext, kw_only=True):
    users: UserService


async def get_context(users: UserService) -> Context:
    return Context(users=users)


@strawberry.type
class Query:
    @strawberry.field
    async def greeting(self, info: strawberry.Info[Context], user_id: int) -> str:
        return await info.context.users.greet(user_id)


schema = strawberry.Schema(query=Query)
GraphQLController = make_graphql_controller(schema, path="/graphql", context_getter=get_context)
app = Litestar([GraphQLController], plugins=[ClientPlugin()])
```

```console
$ uvicorn app.litestar_graphql:app
INFO:     Started server process [56803]
INFO:     Waiting for application startup.
database: connected
INFO - 2026-10-10 18:40:33,849 - nuke_di.core - core - Connected 2 clients in 0.00s (slowest: Database 0.00s, UserService 0.00s)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:53760 - "POST /graphql HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [56803]
```

```console
$ curl localhost:8000/graphql -H 'Content-Type: application/json' -d '{"query": "{ greeting(userId: 42) }"}'
{"data":{"greeting":"Hello, user-42!"}}
```

```python
# tests/test_litestar_graphql.py
from litestar.testing import TestClient

from app.clients import Database
from app.litestar_graphql import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_query() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        response = client.post("/graphql", json={"query": "{ greeting(userId: 1) }"})

    assert response.json() == {"data": {"greeting": "Hello, alice!"}}
```

```console
$ pytest -q -W ignore::DeprecationWarning tests/test_litestar_graphql.py
.                                                                        [100%]
1 passed in 0.37s
```

Las reglas:

- **El context getter es una función.** El `BaseContext` de Strawberry para Litestar es un `Struct` de
  msgspec; pasado directamente como `context_getter=Context`, hace fallar cada petición con un
  `msgspec.ValidationError`.
- **Las suscripciones** corren en el handler websocket del mismo controller, que `ClientPlugin` también
  ve, y reciben el contexto del mismo getter.
- **Las advertencias de deprecación son de Strawberry.** En Litestar 2.24, el controller de Strawberry
  0.332 declara sus propias dependencias, `context`, `root_value`, `response`, solo por nombre, y Litestar
  avisa sobre cada una; por eso la prueba se ejecuta con `-W ignore::DeprecationWarning`. El argumento
  `users` de `get_context` no genera ninguna.
- A diferencia de FastAPI, no hace falta nada más: Litestar le pasa a `ClientPlugin` las dependencias del
  controller tal como son; consulta [FastAPI](fastapi.md#strawberry-graphql) para ver la diferencia.
