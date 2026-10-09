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
