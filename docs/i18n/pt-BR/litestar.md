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

## <a id="differences-from-fastapi"></a>Diferenças em relação ao FastAPI

Um handler é escrito do mesmo jeito nos dois, `users: UserService`. O que muda vem de como cada framework
injeta: o FastAPI lê um `Depends` na assinatura de cada função, o Litestar casa uma dependência pelo nome
do argumento, então o `ClientPlugin` fornece cada cliente sob o nome do seu argumento, na aplicação
([ADR-0004](../../adr/0004-litestar-clients-by-name.md)).

| | FastAPI | Litestar |
|---|---|---|
| Configuração | `setup(app)` antes das rotas; routers via `ClientRouter` | `ClientPlugin()` em `plugins=`; qualquer router ou controller |
| Handlers vistos | Toda rota declarada depois de `setup(app)`, na aplicação ou em um `ClientRouter` ou `APIRouter(route_class=ClientRoute)` incluído | Os handlers com os quais a aplicação é criada; um adicionado depois com `app.register()` não é |
| Nomes de argumento | Livres: `users: UserService` aqui e `users: Billing` ali | Um nome, um cliente na aplicação inteira; dois clientes sob o mesmo nome lançam `TypeError` quando a aplicação é criada |
| Um `NotSingletonClient` (a ser removido, [ADR-0006](../../adr/0006-clients-live-as-long-as-the-container.md)) | Uma instância por argumento | Uma instância por nome de argumento, compartilhada por todos os handlers que usam o nome |
| O que muda na função | O `__signature__` dela; `get_type_hints()` ainda mostra `UserService` | As `__annotations__` dela; `get_type_hints(include_extras=True)` mostra `Annotated[UserService, Dependency(), SkipValidationMarker()]` |
| Websockets | Endpoints `@app.websocket` | Handlers `@websocket`; um websocket listener lança `TypeError` |
| O lifespan da própria aplicação | Roda por dentro: os clientes se conectam antes dele e se desconectam depois dele | Os clientes se conectam antes de `lifespan=` e `on_startup=`, e se desconectam depois de `on_shutdown=` |
| Substituir uma dependência em um teste | `override()`, ou `app.dependency_overrides` | `override()`, ou uma dependência de mesmo nome em uma camada, que tem prioridade sobre o cliente |
| Outro container | `setup(app, container)` e `ClientRouter(container=container)` | `ClientPlugin(container)` |

## <a id="litestar-3"></a>Litestar 3

O Litestar 3 ainda não foi lançado: em 2026-10-10, a versão mais recente no PyPI é a 2.24.0, de
2026-06-11, e não há nenhuma pré-release da 3.0. Duas coisas sobre ele são conhecidas, e o nuke-di não muda
nada até que ele saia:

- **As dependências inferidas deixam de existir.** O Litestar 2.24 emite um aviso para uma dependência
  casada apenas pelo nome: `Inferred dependencies will stop working in Litestar 3.0`. A anotação que o
  nuke-di escreve, `Annotated[UserService, Dependency(), SkipValidationMarker()]`, é o que
  `NamedDependency[...]` e `SkipValidation[...]` representam, a forma explícita, e o Litestar 2.24 não
  emite aviso para nada disso.
- **A injeção por tipo está planejada.** O [anúncio da v3](https://litestar.dev/blog/v3-announcement)
  de 2026-07-26 planeja `TypeDependency[SomeService]` ao lado de `NamedDependency`, com o provider indexado
  pelo tipo, `dependencies={SomeService: provide_some_service}`, e aponta essa reformulação da DI como a
  funcionalidade que ainda separa a 3.0 do beta.

Com providers indexados por tipo, o `ClientPlugin` poderia fornecer cada cliente sob a sua classe em vez
do nome do seu argumento, e as linhas da tabela acima sobre nomes e instâncias deixariam de existir. Se
isso vai acontecer será decidido quando a 3.0 e a API dela saírem, revisitando a ADR-0004. Os handlers não
mudam em nenhum dos casos: eles declaram `users: UserService`, e só o plugin decide como o Litestar fica
sabendo disso.

## <a id="strawberry-graphql"></a>Strawberry GraphQL

O controller do Strawberry para Litestar constrói o contexto do GraphQL com uma dependência do Litestar,
então um context getter recebe clientes como qualquer outra dependência, e os resolvers os leem de
`info.context`:

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

As regras:

- **O context getter é uma função.** O `BaseContext` do Strawberry para Litestar é um `Struct` do
  msgspec; passado diretamente como `context_getter=Context`, ele faz toda requisição falhar com um
  `msgspec.ValidationError`.
- **Subscriptions** rodam no handler de websocket do mesmo controller, que o `ClientPlugin` também vê, e
  recebem o contexto do mesmo getter.
- **Os avisos de depreciação são do Strawberry.** No Litestar 2.24, o controller do Strawberry 0.332
  declara as suas próprias dependências, `custom_context`, `context`, `context_ws`, `root_value` e
  `response`, apenas pelo nome, e o Litestar emite um aviso para cada uma, e é por isso que o teste roda
  com `-W ignore::DeprecationWarning`. O argumento `users` de `get_context` não gera nenhum.
- Nada mais é necessário: o Litestar entrega as dependências do controller ao `ClientPlugin` como elas
  são. No FastAPI, o router recebe `route_class=ClientRoute`; veja
  [FastAPI](fastapi.md#strawberry-graphql).
