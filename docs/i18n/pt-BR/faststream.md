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

## <a id="publishing-from-a-client"></a>Publicando a partir de um cliente

Um cliente que publica, um outbox ou um notificador, recebe o broker da aplicação no `__init__` e deixa o
ciclo de vida dele com o FastStream:

```python
# app/notify.py
from faststream import FastStream
from faststream.nats import NatsBroker

from app.clients import UserService
from nuke_di import Client
from nuke_di.faststream import setup

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
setup(app)


class Notifications(Client):
    # The app's broker: FastStream starts it after the clients connect and stops it before they
    # disconnect, so connect() and disconnect() leave it alone
    def __init__(self, nats: NatsBroker = broker) -> None:
        self._nats = nats

    async def send(self, text: str) -> None:
        await self._nats.publish(text, "notifications")


@broker.subscriber("greetings")
async def greet(user_id: int, users: UserService, notifications: Notifications) -> None:
    await notifications.send(await users.greet(user_id))


@broker.subscriber("notifications")
async def show(text: str) -> None:
    print(f"notification: {text}")
```

```console
$ faststream run app.notify:app
database: connected
2026-10-10 18:39:08,837 INFO     - FastStream app starting...
2026-10-10 18:39:08,842 INFO     - greetings     |            - `Greet` waiting for messages
2026-10-10 18:39:08,843 INFO     - notifications |            - `Show` waiting for messages
2026-10-10 18:39:08,843 INFO     - FastStream app started successfully! To exit, press CTRL+C
2026-10-10 18:39:11,811 INFO     - greetings     | 7c3cf44a-9 - Received
2026-10-10 18:39:11,811 INFO     - greetings     | 7c3cf44a-9 - Processed
2026-10-10 18:39:11,812 INFO     - notifications | 5ddab782-7 - Received
notification: Hello, user-42!
2026-10-10 18:39:11,812 INFO     - notifications | 5ddab782-7 - Processed
^C
2026-10-10 18:39:12,908 INFO     - FastStream app shutting down...
2026-10-10 18:39:12,909 INFO     - FastStream app shut down gracefully.
database: disconnected
```

A mensagem foi publicada com o `publish.py` acima. Em um teste, o broker de teste roteia o que o cliente
publica como qualquer outra mensagem, ou o cliente é substituído:

```python
# tests/test_notify.py
from faststream import TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.notify import Notifications, app, broker, show
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


class FakeNotifications(Notifications):
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


async def test_greet_publishes() -> None:
    with DI.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(1, "greetings")

            show.mock.assert_called_once_with("Hello, alice!")


async def test_greet_with_fake_notifications() -> None:
    fake = FakeNotifications()
    with DI.override(Notifications, fake):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            await test_broker.publish(42, "greetings")

    assert fake.sent == ["Hello, user-42!"]
```

```console
$ pytest -q tests/test_notify.py
..                                                                       [100%]
2 passed in 0.19s
```

As regras:

- **O broker é da aplicação.** O FastStream o inicia depois que os clientes se conectam e o para antes que
  eles se desconectem, então este cliente não cria o seu broker no `connect()`, como um cliente de um
  objeto de terceiros faria em outros casos
  ([ADR-0005](../../adr/0005-third-party-objects-as-client-classes.md)): o broker pertence ao FastStream.
  Um cliente publica a partir dos seus métodos, enquanto a aplicação está rodando.
- **Não a partir do `connect()` ou do `disconnect()`.** Em um broker real, um `publish()` no `connect()`
  lança `faststream.exceptions.IncorrectState`, já que o broker ainda não iniciou, e a aplicação não
  consegue iniciar, com
  `RuntimeError: nuke-di clients failed to start: Notifications.connect() raised IncorrectState`. No
  `disconnect()` ele lança o mesmo erro, já que o broker já parou, mas o nuke-di apenas registra em log um
  `disconnect()` que falhou, e a aplicação termina normalmente. Sob o `TestNatsBroker`, o primeiro lança
  ``SetupError: You should setup `HandlerItem` at first.`` e o segundo passa em silêncio, então os testes
  não detectam um publish no `disconnect()`.
- **O broker é um argumento com valor padrão**, não um cliente: o nuke-di preenche os argumentos tipados
  como clientes e deixa os outros com os seus valores padrão. Um teste unitário pode construir
  `Notifications(nats=AsyncMock())`.
- **Sob o `TestNatsBroker`** o mesmo objeto broker é modificado, então o cliente publica em memória e
  `show.mock` vê a mensagem; `override(Notifications, ...)` substitui o cliente para todo subscriber que o
  recebe.
- **Um processo sem uma aplicação FastStream**, por exemplo um `@job` que envia mensagens, é dono da
  própria conexão: ali o broker é criado no `connect()` do cliente e parado no `disconnect()` dele, como
  `Nats` em [examples/faststream_nats/publish.py](../../../examples/faststream_nats/publish.py).
- **Vários brokers**, `FastStream(first, second)`, funcionam do mesmo jeito: `setup(app)` preenche os
  subscribers de cada um deles, e um cliente recebe como valor padrão o broker para o qual publica.

## <a id="one-app-at-a-time-in-tests"></a>Uma aplicação por vez, nos testes

O FastStream constrói um subscriber de novo a cada inicialização, então o nuke-di reescreve uma função
subscriber uma única vez, qualquer que seja o container (`per_container=False` em
[Escrevendo uma integração](integrations.md)), e cada aplicação que inicia a preenche a partir do próprio
container. Por isso, testes que iniciam aplicações uma depois da outra podem dar a cada uma um container
próprio, sobre o broker no nível do módulo:

```python
# tests/test_containers.py
from faststream import FastStream, TestApp
from faststream.nats import TestNatsBroker

from app.clients import Database
from app.notify import broker, show
from nuke_di import Dependencies
from nuke_di.faststream import setup


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def make_app(container: Dependencies) -> FastStream:
    # A new app on the module-level broker, whose subscribers are declared on import
    app = FastStream(broker)
    setup(app, container)
    return app


async def test_greet(di: Dependencies) -> None:
    with di.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(make_app(di)):
            await test_broker.publish(1, "greetings")

            show.mock.assert_called_once_with("Hello, alice!")
```

```console
$ pytest -q tests/test_containers.py
.                                                                        [100%]
1 passed in 0.15s
```

O que fazer a respeito:

- **Rode os testes que iniciam uma aplicação um depois do outro**, que é o que o pytest faz. O
  pytest-xdist os roda em processos próprios, que não compartilham nada.
- **Não inicie ao mesmo tempo duas aplicações sobre as mesmas funções subscriber**, por exemplo um
  `TestApp` dentro de outro. O segundo falha ao iniciar com `RuntimeError: nuke-di clients
  failed to start: UserService is filled for another app that is running; apps that share a handler
  function run one at a time`, em vez de entregar à segunda aplicação os clientes da primeira.
- **`DI.override()` na aplicação do nível do módulo ou um container por teste**: os dois servem; escolha
  pelo que o resto dos testes usa.
- Diferente disso, uma requisição do FastAPI recebe os clientes da aplicação à qual chegou, então
  aplicações FastAPI em containers diferentes servem as mesmas funções ao mesmo tempo; veja
  [FastAPI](fastapi.md#an-app-per-test-container).
