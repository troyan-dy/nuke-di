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
