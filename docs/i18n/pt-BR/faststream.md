# <a id="faststream"></a>FastStream

[English](../../guide/faststream.md) · [Русский](../ru/faststream.md) · [简体中文](../zh-CN/faststream.md) · [Español](../es/faststream.md) · **Português (Brasil)** · [日本語](../ja/faststream.md) · [Polski](../pl/faststream.md)

← [Documentação](../README.pt-BR.md#documentation)

Um subscriber do FastStream recebe um cliente pelo type hint, ao lado da mensagem:

```bash
pip install "nuke-di[faststream]"
```

Requer FastStream 0.6 ou mais recente, com qualquer broker. Com os clientes dos exemplos de [FastAPI](fastapi.md):

```python
# app/worker.py
from faststream import FastStream
from faststream.nats import NatsBroker

from app.clients import UserService
from nuke_di.faststream import setup

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
setup(app)  # clients connect before the broker starts, disconnect after it stops


@broker.subscriber("greetings")
async def greet(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))
```

```console
$ faststream run app.worker:app
database: connected
2026-10-08 15:12:52,281 INFO     - FastStream app starting...
2026-10-08 15:12:52,287 INFO     - greetings |            - `Greet` waiting for messages
2026-10-08 15:12:52,287 INFO     - FastStream app started successfully! To exit, press CTRL+C
2026-10-08 15:12:55,078 INFO     - greetings | a747e4d0-2 - Received
Hello, user-42!
2026-10-08 15:12:55,079 INFO     - greetings | a747e4d0-2 - Processed
^C
2026-10-08 15:12:56,222 INFO     - FastStream app shutting down...
2026-10-08 15:12:56,223 INFO     - FastStream app shut down gracefully.
database: disconnected
```

A mensagem foi publicada com:

```python
# publish.py
import asyncio

from faststream.nats import NatsBroker


async def main() -> None:
    async with NatsBroker("nats://localhost:4222") as broker:
        await broker.publish(42, "greetings")


asyncio.run(main())
```

As regras:

- **Onde os clientes são preenchidos.** Nos argumentos dos subscribers dos brokers da aplicação, inclusive os
  dos routers incluídos, e de todo `Depends(...)` que eles usam, em qualquer profundidade: funções e classes,
  incluindo `dependencies=` do subscriber, do seu router e do broker. Todos os outros argumentos são do
  FastStream: a mensagem, os campos dela, `Context()`.
- **Quais clientes sobem.** Na inicialização, os de todos os subscribers que os brokers da aplicação servem,
  incluindo os dos routers. Os subscribers podem ser declarados antes ou depois de `setup(app)`.
- **Lifespan.** Os clientes se conectam antes dos hooks `lifespan=` e `on_startup=` da própria aplicação e antes
  que os brokers iniciem; eles se desconectam depois que os brokers param e depois dos hooks `after_shutdown=`.
  `Shutdown` e `BackgroundTasks` se comportam como no [FastAPI](fastapi.md). `setup()` também funciona em um
  `AsgiFastStream`.
- **Instâncias.** Assim como em `inject()`, um `Client` é uma instância por container, e um
  `NotSingletonClient` é uma instância por argumento que o declara, não uma por mensagem.
- **A função continua sendo uma função.** A assinatura dela mostra `Annotated[UserService, Depends(...)]` para o
  FastStream, como no [FastAPI](fastapi.md).
- **Uma aplicação por vez.** Uma função subscriber e as dependências dela são reescritas uma única vez,
  qualquer que seja o container, então aplicações que as compartilham, por exemplo uma aplicação por teste
  sobre um broker no nível do módulo, rodam uma depois da outra: uma aplicação que inicia enquanto outra
  com a mesma função está rodando falha ao iniciar. Uma função de dependência que recebe clientes serve
  handlers do FastAPI ou do FastStream, não dos dois.

**Testes.** O broker de teste do FastStream não roda nenhum hook da aplicação, então inicie a aplicação com `TestApp` dentro dele:

```python
# tests/test_worker.py
import pytest
from faststream import TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.worker import app, broker
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_greet(capsys: pytest.CaptureFixture[str]) -> None:
    with DI.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(1, "greetings")

    assert "Hello, alice!" in capsys.readouterr().out
```

```console
$ pytest -q tests/test_worker.py
.                                                                        [100%]
1 passed in 0.14s
```

Uma mensagem processada sem o lifespan da aplicação, por exemplo via `TestNatsBroker(broker)` sem `TestApp`,
lança `RuntimeError: UserService is not connected: start the app with its lifespan`. Um subscriber
adicionado depois que a aplicação já iniciou lança `RuntimeError: UserService was not started with the app`.
