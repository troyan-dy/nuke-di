# <a id="starlette-quart-and-any-asgi-app"></a>Starlette, Quart e qualquer aplicação ASGI

[English](../../guide/asgi.md) · [Русский](../ru/asgi.md) · [简体中文](../zh-CN/asgi.md) · [Español](../es/asgi.md) · **Português (Brasil)** · [日本語](../ja/asgi.md) · [Polski](../pl/asgi.md)

← [Documentação](../README.pt-BR.md#documentation)

Um framework sem injeção de dependências, como Starlette ou Quart, não consegue preencher os argumentos de um
handler pelo type hint. `nuke_di.asgi.lifespan()` cobre o resto: ele é o lifespan da aplicação, que conecta os
clientes listados quando a aplicação inicia e os desconecta quando ela para, e um handler pede um cliente a ele
com `get()`. Ele não importa nenhum framework e não precisa de extras:

```bash
pip install nuke-di
```

- [Starlette](#starlette)
- [O lifespan da própria aplicação](#the-apps-own-lifespan)
- [Testes](#testing)
- [Quart](#quart)
- [aiohttp](#aiohttp)
- [Uma aplicação ASGI sem framework](#a-plain-asgi-app)
- [Erros](#errors)

## <a id="starlette"></a>Starlette

Com os clientes dos exemplos de [FastAPI](fastapi.md):

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

As regras:

- **A lista é explícita.** Não há uma tabela de rotas onde procurar os clientes, então
  `lifespan(container, *clients)` nomeia cada cliente que um handler recebe. `clients.get(Database)` só
  funciona quando `Database` está na lista, mesmo que ele se conecte de qualquer forma como dependência de
  `UserService`: um handler que contasse com isso quebraria no dia em que `UserService` deixasse de depender dele.
- **`get()` é tipado.** `clients.get(UserService)` retorna um `UserService` para o mypy e o pyright, ao
  contrário de um atributo de `request.state`. É um método comum, então funciona em um handler, em um endpoint
  de websocket, em um middleware ou em uma tarefa em segundo plano, e os handlers importam o módulo que contém
  `clients` como qualquer outro.
- **Resolvido na inicialização.** Criar `clients` não resolve nada; cada inicialização resolve de novo os
  clientes listados, então um `override()` antes de a aplicação iniciar substitui um deles, e uma segunda
  inicialização recebe clientes novos.
- **Encerramento.** No encerramento, `Shutdown` é acionado, as `BackgroundTasks` são paradas e depois os
  clientes se desconectam, na mesma ordem que em um [worker](workers-and-jobs.md).
- **Uma aplicação por vez.** Um container se conecta uma única vez: uma segunda aplicação iniciada no mesmo
  container enquanto a primeira está rodando falha na inicialização.
- **FastAPI também.** Uma aplicação FastAPI que mantém as assinaturas das suas rotas como foram escritas passa
  `FastAPI(lifespan=clients)` da mesma forma; com `nuke_di.fastapi.setup()`, as rotas dela recebem os clientes
  pelo type hint, veja [FastAPI](fastapi.md).

## <a id="the-apps-own-lifespan"></a>O lifespan da própria aplicação

`clients(app)` é um gerenciador de contexto assíncrono, então o lifespan da própria aplicação entra nele
primeiro e roda lá dentro o seu código de inicialização e de encerramento, com os clientes conectados. O que
ele entrega com `yield` é o estado da aplicação, como de costume:

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

## <a id="testing"></a>Testes

Um teste substitui um cliente antes que o `TestClient` inicie a aplicação:

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

`TestClient(app)` sem `with` envia requisições sem rodar o lifespan, e o handler avisa:

```console
$ python -c "from starlette.testclient import TestClient; from app.starlette_api import app; TestClient(app).get('/users/1')"
Traceback (most recent call last):
  ...
RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` in Starlette or `async with app.test_app()` in Quart
```

## <a id="quart"></a>Quart

O Quart não tem um argumento `lifespan=`: ele roda os hooks `before_serving` na inicialização e os hooks
`after_serving` no encerramento, cada tipo na ordem em que foi registrado, e pula os demais quando um falha.
Uma subclasse de `Quart` conecta os clientes em volta de todos eles, então qualquer hook da aplicação pode
usar os clientes, e um hook que falha ainda assim os deixa desconectados:

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

Um teste inicia a aplicação com `test_app()`:

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

Por que não dois hooks da aplicação, um que conecta e outro que desconecta? Cada um rodaria na sua vez entre
os hooks da própria aplicação. Um `disconnect` em `after_serving` roda antes dos hooks `after_serving`
registrados depois dele, que então encontram os clientes desconectados; um hook `before_serving` que falha
depois daquele que conectou deixa o container conectado, porque o Quart então não chama nenhum hook
`after_serving`, e a próxima inicialização falha com `the container is already connected`.
`Quart.startup()` e `Quart.shutdown()` rodam todos os hooks, então a subclasse conecta antes do primeiro e
desconecta depois do último, qualquer que seja o que falhe. O `while_serving` do Quart ficaria mais curto,
mas ele cria o seu gerador uma única vez, no registro, então a aplicação só poderia iniciar uma vez por
processo, e o segundo teste que a inicia falharia.

## <a id="aiohttp"></a>aiohttp

O aiohttp 3.14 aceita um gerenciador de contexto assíncrono em `cleanup_ctx`, e `clients` é um:

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

Um aiohttp mais antigo aceita, em vez disso, um gerador assíncrono:

```python
from collections.abc import AsyncIterator


async def run_clients(app: web.Application) -> AsyncIterator[None]:
    async with clients(app):
        yield


app.cleanup_ctx.append(run_clients)
```

## <a id="a-plain-asgi-app"></a>Uma aplicação ASGI sem framework

Uma aplicação ASGI sem framework responde ela mesma às mensagens de lifespan do servidor. Ali, `clients()` não
recebe argumentos:

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

## <a id="errors"></a>Erros

| Quando | O que é lançado |
|---|---|
| Um handler roda sem o lifespan da aplicação, ou depois que ele parou | ``RuntimeError: UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` in Starlette or `async with app.test_app()` in Quart `` |
| `get()` de um cliente que não está na lista | ``RuntimeError: Database is not a client of this lifespan: list it in `lifespan(container, ...)` `` |
| O `connect()` de um cliente falha | `RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused`; o servidor reporta uma falha na inicialização e termina, e o container fica esvaziado com `flush()` |
| O container já está conectado, por exemplo por outra aplicação | `RuntimeError: nuke-di clients failed to start: the container is already connected` |
| `lifespan(Database)`, sem o container | `TypeError: lifespan() takes the container first, then the clients, e.g. lifespan(DI, Database); got <class 'app.clients.Database'>` |
| `lifespan(DI, "Database")`, que não é uma classe de cliente | `TypeError: 'Database' is not a client: subclass Client or NotSingletonClient` |

No uvicorn, com um `Database` cujo `connect()` lança `OSError("connection refused")`:

```console
$ uvicorn app.broken_api:app
INFO:     Started server process [44319]
INFO:     Waiting for application startup.
Database.connect() raised OSError: connection refused
...
RuntimeError: nuke-di clients failed to start: Database.connect() raised OSError: connection refused

ERROR:    Application startup failed. Exiting.
```
