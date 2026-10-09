# <a id="litestar"></a>Litestar

[English](../../guide/litestar.md) · [Русский](../ru/litestar.md) · [简体中文](../zh-CN/litestar.md) · [Español](../es/litestar.md) · **Português (Brasil)** · [日本語](../ja/litestar.md) · [Polski](../pl/litestar.md)

← [Documentação](../README.pt-BR.md#documentation)

Um route handler do Litestar também recebe um cliente pelo type hint, por meio de um plugin:

```bash
pip install "nuke-di[litestar]"
```

Requer Litestar 2.15 ou mais recente. Com os clientes dos exemplos de [FastAPI](fastapi.md):

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

`ClientPlugin()` encontrou `users: UserService` em `get_user` e `db: Database` na dependência
`current_user`, forneceu ambos ao Litestar como dependências e os conectou na inicialização.

As regras:

- **Onde os clientes são preenchidos.** Nos argumentos dos handlers HTTP e `@websocket` com os quais a aplicação é
  criada, incluindo os de routers e controllers em qualquer profundidade, e de todas as dependências
  declaradas na aplicação, em um router, em um controller ou em um handler: funções e classes.
- **Por nome.** O Litestar fornece dependências pelo nome do argumento, então o nuke-di fornece cada argumento de
  cliente sob o seu nome, na aplicação. Um nome significa um cliente na aplicação inteira: `users: UserService`
  em um handler e `users: Billing` em outro lançam `TypeError` quando a aplicação é criada. Uma
  dependência de mesmo nome declarada pela aplicação, por um router, por um controller ou por um handler tem
  prioridade sobre o cliente.
- **Instâncias.** Um `Client` é uma instância por container; um `NotSingletonClient` é uma instância por
  nome de argumento.
- **Lifespan.** Os clientes se conectam antes que o `lifespan=` e o `on_startup=` da própria aplicação rodem, e
  se desconectam depois dos hooks `on_shutdown=` dela, que o Litestar chama por último. `Shutdown` e
  `BackgroundTasks` se comportam como no [FastAPI](fastapi.md).
- **A função continua sendo uma função.** Os argumentos de cliente dela agora são anotados como
  dependências explícitas do Litestar cujo valor não é validado,
  `Annotated[UserService, Dependency(), SkipValidationMarker()]`, que é o que o Litestar 2.23 exige em vez
  de uma dependência casada apenas pelo nome. Chamar a função diretamente funciona como antes.
- **Plugins.** Coloque o `ClientPlugin()` depois de qualquer plugin que adicione route handlers: ele vê os
  handlers que a aplicação tem quando chega a vez dele.
- **Outro container.** `ClientPlugin(container)`.

**Testes.** Assim como no FastAPI, um teste substitui um cliente antes que o `TestClient` inicie a aplicação:

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

**Não suportado.** Um websocket listener, `@websocket_listener` ou uma classe `WebsocketListener`, não aceita
clientes: o Litestar lê a assinatura dele no momento em que ele é declarado, antes que o plugin o veja, então a aplicação
lança `TypeError` e indica um handler `@websocket` em vez disso. Um argumento de cliente com um nome que o
Litestar reserva, como `state` ou `request`, também lança `TypeError`. Um handler registrado depois que a
aplicação é criada, com `app.register()`, não é visto.
