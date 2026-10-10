# <a id="fastapi"></a>FastAPI

[English](../../guide/fastapi.md) · [Русский](../ru/fastapi.md) · [简体中文](../zh-CN/fastapi.md) · [Español](../es/fastapi.md) · **Português (Brasil)** · [日本語](../ja/fastapi.md) · [Polski](../pl/fastapi.md)

← [Documentação](../README.pt-BR.md#documentation)

Uma operação de rota do FastAPI recebe um cliente do mesmo jeito que um job: pelo type hint. Não é preciso
escrever mais nada em cada handler: nada de `Depends`, nada de `inject()`.

```bash
pip install "nuke-di[fastapi]"
```

Requer FastAPI 0.105 ou mais recente. Os exemplos compartilham um mesmo módulo de clientes:

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

A API:

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

O que aconteceu:

1. `setup(app)` fez com que toda rota declarada em `app` a partir daí preencha os seus argumentos de cliente a partir do
   `DI` global, e envolveu o lifespan da aplicação.
2. `@app.get` viu `users: UserService` e apenas registrou isso; nada foi construído no import.
3. Na inicialização, o lifespan resolveu os clientes das rotas que a aplicação serve, tanto as dela quanto as
   dos routers que ela inclui, e os conectou, cada um depois das suas dependências. No encerramento,
   desconectou-os.
4. Uma requisição para `/users/42` recebeu o `UserService` já conectado. `/me` passou pela dependência
   `current_user`, que recebe `db: Database` da mesma forma.

As regras:

- **Onde os clientes são preenchidos.** Nos argumentos das operações de rota, dos endpoints de websocket e de todas as
  dependências que eles usam, em qualquer profundidade: funções e classes usadas como `Depends(Auth)` ou `Annotated[Auth, Depends()]`,
  incluindo `dependencies=` da rota, do seu router, de `include_router()` e da aplicação. Um
  argumento é um cliente quando o seu type hint é um cliente, inclusive dentro de `Annotated[UserService, ...]`
  sem `Depends`. Todos os outros argumentos são do FastAPI: path, query, header, body, `Depends`.
- **Routers.** Crie-os com `ClientRouter(...)`, que aceita os mesmos argumentos que `APIRouter`,
  e inclua-os na aplicação ou em outro `ClientRouter`. `APIRouter(route_class=ClientRoute)`
  funciona para um router que não inclui outros routers. Para usar outro container, use
  `setup(app, container)` e `ClientRouter(container=container)`; incluir um router de outro
  container lança `TypeError` imediatamente.
- **Chame `setup(app)` antes das rotas.** Uma rota com cliente declarada antes disso falha imediatamente
  com o `TypeError` descrito [abaixo](#not-supported).
- **Só o que a aplicação serve.** Um router que a aplicação não inclui, por exemplo um importado apenas por um
  teste, não conecta nada na inicialização da aplicação.
- **Um container por aplicação.** Cada aplicação recebe os clientes do container passado ao `setup()` dela, então
  duas aplicações sobre dois containers servem as mesmas funções ao mesmo tempo, por exemplo em testes. Uma
  aplicação montada em uma aplicação com `setup()`, ou uma que serve as rotas dela, por exemplo via
  `include_router(api.router)`, que roda o lifespan de `api`, recebe os clientes que essa aplicação iniciou.
- **Instâncias.** Assim como em `inject()`, um `Client` é uma instância por container, e um
  `NotSingletonClient` é uma instância por argumento que o declara, não uma por requisição.
- **Lifespan.** O `lifespan=` da própria aplicação roda por dentro: o código de inicialização dele já vê os clientes conectados, e
  o código de encerramento roda antes que eles se desconectem. No encerramento, `Shutdown` é acionado e
  `BackgroundTasks` é parado, se a aplicação usar esses clientes, antes que os clientes se desconectem, como em um
  worker. O `BackgroundTasks` do próprio FastAPI é outra classe e não é um cliente.
- **A função continua sendo uma função.** A assinatura dela agora mostra `Annotated[UserService, Depends(...)]`
  para o FastAPI, mas chamá-la diretamente com um cliente, por exemplo em um teste unitário, funciona como antes.

**Testes.** Importar a aplicação não constrói nada, então um teste substitui um cliente antes que o `TestClient`
inicie a aplicação, com [`override()`](testing.md) ou com a fixture `global_di`:

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

`app.dependency_overrides` continua funcionando, inclusive para uma função de dependência que recebe clientes.

**Websockets.** Um endpoint de websocket recebe clientes do mesmo jeito, na aplicação ou em um `ClientRouter`:

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

**Um cliente que não consegue se conectar** faz a inicialização falhar. O lifespan lança um `RuntimeError` comum
a partir do `ConnectError`, já que um `SystemExit` escaparia do event loop do servidor, e o servidor
reporta o erro e termina:

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

## <a id="not-supported"></a>Não suportado

Estes lugares não aceitam clientes. Cada um lança um `TypeError` explicando isso quando a rota é declarada:

| Lugar                                                   | Em vez disso                                  |
|---------------------------------------------------------|-----------------------------------------------|
| Um router criado sem `ClientRouter` / `ClientRoute`     | Crie-o com `ClientRouter(...)`                |
| Um endpoint de websocket em `APIRouter(route_class=ClientRoute)` | Crie o router com `ClientRouter(...)` |
| Um cliente opcional, `Database \| None`                 | Um `Database` simples                         |
| Um método vinculado (bound method) ou um objeto chamável como endpoint ou dependência | Uma função ou uma classe |

Diferente desses casos, uma rota de um router incluído em um `APIRouter` comum, em vez de em um `ClientRouter`,
só é detectada pelas versões mais antigas do FastAPI. No FastAPI 0.14x ela é declarada e a aplicação inicia, mas as requisições dela falham
com `RuntimeError: UserService was not started with the app: include the router of its route into
the app or into a ClientRouter, not into a plain APIRouter`.

Uma requisição que chega sem o lifespan, por exemplo via `TestClient(app)` sem `with`, recebe um
`RuntimeError`: `UserService is not connected: start the app with its lifespan`.

## <a id="class-based-views"></a>Views baseadas em classes

Rotas que compartilham aquilo de que precisam, o usuário da requisição e alguns clientes, recebem isso
como uma única classe. A classe é uma dependência do FastAPI com um `__init__` com type hints, escrita do
mesmo jeito que um cliente, e o `__init__` dela recebe dados da requisição e clientes lado a lado:

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

Um teste substitui um cliente uma única vez para todas as rotas que usam a classe:

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

As regras:

- **Uma view por requisição, um cliente por container.** O FastAPI constrói um `Account` a cada
  requisição; o `db` e o `users` dentro dele são os clientes conectados do container, os mesmos objetos
  em todas as requisições. O nuke-di reescreveu a assinatura da classe, como faz com uma função de
  dependência, e deixou o `__init__` dela intacto: `Account(x_user_id=7, db=db, users=users)` em um teste
  unitário funciona como antes. Uma dataclass funciona do mesmo jeito, com os campos dela como argumentos.
- **Uma view que não precisa de nada da requisição é um cliente.** `class Account(Client)` recebido como
  `account: Account` é construído uma vez por container e se conecta junto com os outros. Uma classe de
  cliente escrita como `Annotated[Account, Depends()]`, por exemplo uma decorada com `@client_dataclass`,
  passa a ser construída pelo FastAPI a cada requisição, e o `connect()` dela nunca roda: deixe o
  `Depends()` de fora.
- **O `@cbv` do fastapi-utils não é necessário.** Ele declara as rotas de novo em um `APIRouter` comum
  próprio, então um atributo de classe tipado como cliente falha em `include_router()` com
  `TypeError: Database is a nuke-di client, not a pydantic type`. A classe acima compartilha os clientes
  entre as rotas usando só o FastAPI.

## <a id="an-app-per-test-container"></a>Uma aplicação por container de teste

Uma factory de aplicação constrói a aplicação em torno do container que recebe, então cada teste roda em
um container próprio e o servidor no `DI` global. As funções ficam no nível do módulo:

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

Os testes constroem uma aplicação sobre a fixture `di`:

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

As regras:

- **Um único `override()` para a aplicação inteira.** `di.override(Database, FakeDatabase())` substitui o
  banco de dados para a dependência `current_user`, para o `UserService` e para tudo o mais que recebe um
  `Database`, enquanto o FastAPI sozinho precisa de uma entrada em `app.dependency_overrides` para cada
  função de dependência.
- **Uma função em vários containers.** `get_user` e `current_user` são reescritos uma única vez; cada
  aplicação resolve os clientes deles no seu próprio container na inicialização, e uma requisição recebe
  os clientes da aplicação à qual chegou. Aplicações construídas em containers diferentes servem as mesmas
  funções, uma depois da outra ou ao mesmo tempo.
- **Um router por aplicação.** Um `ClientRouter` preenche clientes a partir de um único container; uma
  aplicação que inclui um router de outro container lança `TypeError: the router fills clients from another container than
  this app`. Crie os routers dentro da factory.
- **`app.dependency_overrides`** pertence a uma única aplicação e continua funcionando, inclusive para uma
  dependência que recebe clientes, como o `current_user` acima.

## <a id="strawberry-graphql"></a>Strawberry GraphQL

O router do Strawberry para FastAPI, `GraphQLRouter`, é um `APIRouter`, e as rotas dele constroem o
contexto do GraphQL com uma dependência do FastAPI. O contexto é então uma classe com um `__init__` com
type hints que recebe os clientes, e os resolvers os leem de `info.context`:

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

Uma query, e uma subscription pelo websocket do mesmo router:

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

As regras:

- **O contexto recebe os clientes, os resolvers recebem o contexto.** O FastAPI constrói um `Context` a
  cada requisição e a cada conexão de websocket, com os clientes conectados do container dentro;
  `strawberry.Info[Context]` dá aos resolvers o tipo dele. Um resolver não recebe nenhum cliente por type
  hint: o Strawberry não tem injeção de dependências própria, e `info.context` é a forma como ele repassa
  as coisas.
- **`route_class=ClientRoute`** faz as rotas do router preencherem clientes, como em qualquer `APIRouter`.
  O Strawberry entrega o context getter ao FastAPI envolvido em uma dependência própria, e o nuke-di a
  segue até `Context`.
- **Subscriptions.** O FastAPI constrói a rota de websocket do router sem a route class, como para
  qualquer websocket em um `APIRouter`, e mesmo assim ela recebe um `Context` com os clientes: ela
  compartilha a dependência de contexto do Strawberry com as rotas GET e POST, que o router declara
  primeiro por meio do `ClientRoute`. Elas reescrevem `Context` e trazem os clientes dele para a
  inicialização da aplicação. Um endpoint de websocket seu em um router assim ainda lança `TypeError`,
  veja [Não suportado](#not-supported).
- **Outro container**: `route_class=app.router.route_class`, depois de `setup(app, container)`.
- O controller do Strawberry para Litestar recebe clientes por meio do `ClientPlugin`, veja
  [Litestar](litestar.md#strawberry-graphql).
