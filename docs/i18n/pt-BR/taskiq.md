# <a id="taskiq"></a>taskiq

[English](../../guide/taskiq.md) · [Русский](../ru/taskiq.md) · [简体中文](../zh-CN/taskiq.md) · [Español](../es/taskiq.md) · **Português (Brasil)** · [日本語](../ja/taskiq.md) · [Polski](../pl/taskiq.md)

← [Documentação](../README.pt-BR.md#documentation)

Uma tarefa do taskiq recebe um cliente pelo type hint, ao lado dos argumentos com que é enfileirada:

```bash
pip install "nuke-di[taskiq]"
```

Requer taskiq 0.11 ou mais recente, com qualquer broker. Com os clientes dos exemplos de [FastAPI](fastapi.md)
e um `InMemoryBroker`, que executa as tarefas no mesmo processo que as enfileira:

```python
# app/tasks.py
from typing import Annotated

from taskiq import Context, InMemoryBroker, TaskiqDepends

from app.clients import Database, UserService
from nuke_di.taskiq import setup

broker = InMemoryBroker()  # runs the tasks in this process
setup(broker)  # clients connect when the worker starts, disconnect when it stops


@broker.task
async def send_report(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))


async def account_name(context: Annotated[Context, TaskiqDepends()], db: Database) -> str:
    # A dependency gets taskiq's own objects and clients side by side
    return await db.fetch_user(context.message.kwargs["account_id"])


@broker.task
async def close_account(account_id: int, name: Annotated[str, TaskiqDepends(account_name)]) -> str:
    return f"closed the account of {name}"
```

```python
# app/main.py
import asyncio

from app.tasks import broker, close_account, send_report


async def main() -> None:
    # An InMemoryBroker is its own worker: its startup connects the clients
    await broker.startup()
    report = await send_report.kiq(42)
    await report.wait_result()
    closed = await close_account.kiq(account_id=7)
    print((await closed.wait_result()).return_value)
    await broker.shutdown()


asyncio.run(main())
```

```console
$ python -m app.main
database: connected
Hello, user-42!
closed the account of user-7
database: disconnected
```

**Verificadores de tipo.** O taskiq tipa `.kiq()` com a assinatura da própria tarefa, então mypy e pyright
pedem `users` em `send_report.kiq(42)`; o mesmo vale para qualquer argumento que o taskiq preenche, com ou sem
`Annotated`. Um valor padrão torna o argumento opcional para eles, e o cliente continua vindo do container
pelo tipo:

```python
@broker.task
async def send_report(user_id: int, users: UserService = TaskiqDepends()) -> None:
    print(await users.greet(user_id))
```

A regra `B008` do Ruff aponta uma chamada em um valor padrão; os marcadores do taskiq são seguros ali:

```toml
# pyproject.toml
[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["taskiq.TaskiqDepends"]
```

**Um worker de verdade.** Em produção o broker é o de uma fila, por exemplo o `NatsBroker` do
[taskiq-nats](https://github.com/taskiq-python/taskiq-nats); nada mais muda:

```python
# app/tasks.py
import os

from taskiq_nats import NatsBroker

from app.clients import UserService
from nuke_di.taskiq import setup

broker = NatsBroker(os.environ.get("NATS_URL", "nats://localhost:4222"), queue="reports")
setup(broker)


@broker.task
async def send_report(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))
```

O processo do worker conecta os clientes; um processo que só enfileira tarefas, como uma aplicação web,
conecta o broker e nada mais:

```python
# app/kick.py
import asyncio

from app.tasks import broker, send_report


async def main() -> None:
    # A client process: the broker connects to NATS, the clients stay unconnected
    await broker.startup()
    await send_report.kiq(42)
    print("kicked send_report(42)")
    await broker.shutdown()


asyncio.run(main())
```

```console
$ taskiq worker app.tasks:broker --workers 1
[2026-10-10 18:40:00,853][taskiq.worker][INFO   ][MainProcess] Pid of a main process: 56250
[2026-10-10 18:40:00,853][taskiq.worker][INFO   ][MainProcess] Starting 1 worker processes.
[2026-10-10 18:40:00,859][taskiq.process-manager][INFO   ][MainProcess] Started process worker-0 with pid 56252
database: connected
[2026-10-10 18:40:01,084][nuke_di.core][INFO   ][worker-0] Connected 2 clients in 0.00s (slowest: Database 0.00s, UserService 0.00s)
[2026-10-10 18:40:01,091][taskiq.receiver.receiver][INFO   ][worker-0] Listening started.
[2026-10-10 18:40:03,815][taskiq.receiver.receiver][INFO   ][worker-0] Executing task app.tasks:send_report with ID: e987ddad37444fa5a0ade0174578c9f2
Hello, user-42!
^C
[2026-10-10 18:40:05,845][taskiq.process-manager][INFO   ][MainProcess] Workers are scheduled for shutdown.
[2026-10-10 18:40:05,995][taskiq.process-manager][INFO   ][MainProcess] Stopped process worker-0 with pid 56252
[2026-10-10 18:42:01,140][taskiq.receiver.receiver][INFO   ][worker-0] Stopping prefetching messages...
[2026-10-10 18:42:01,143][taskiq.receiver.receiver][INFO   ][worker-0] The runner is stopped.
[2026-10-10 18:42:01,144][taskiq.worker][INFO   ][worker-0] Shutting down the broker.
database: disconnected
```

Em outro terminal:

```console
$ python -m app.kick
kicked send_report(42)
```

Os dois minutos antes de `Stopping prefetching messages...` são do taskiq: o processo do worker (taskiq 0.13
com taskiq-nats 0.7) só percebe o sinal na próxima mensagem ou no próximo ping do NATS.

As regras:

- **Onde os clientes são preenchidos.** Nos argumentos das tarefas do broker e de toda função
  `TaskiqDepends(...)` que elas usam, em qualquer profundidade, incluindo dependências geradoras. Todos os
  outros argumentos são do taskiq: os argumentos de `.kiq()`, `Context`, `TaskiqState`. Uma classe de
  dependência, `Annotated[Auth, TaskiqDepends()]`, é construída pelo taskiq a partir do próprio `__init__`,
  que o nuke-di não reescreve: uma classe que recebe clientes é ela mesma um cliente, ou os recebe por meio de
  uma função de dependência.
- **Quais clientes sobem.** Os de todas as tarefas em `broker.get_all_tasks()`: as do próprio broker e as
  compartilhadas (`@shared_task`). As tarefas podem ser declaradas antes ou depois de `setup(broker)`; as
  compartilhadas, só antes: elas são registradas no broker compartilhado do taskiq, que `setup()` não
  intercepta.
- **Qual processo conecta.** Aquele em que o broker dispara `WORKER_STARTUP`: um processo `taskiq worker` e
  qualquer processo que inicie um `InMemoryBroker`, que é o próprio worker. Um processo que só enfileira
  tarefas inicia o broker com `CLIENT_STARTUP` e não conecta nenhum cliente. Assim, um único módulo com o
  broker serve aos dois: o worker conecta pelo taskiq, a aplicação web pela própria integração.
- **Lifespan.** Os clientes se conectam antes dos demais handlers de `WORKER_STARTUP`, incluindo os
  registrados antes de `setup()`, e se desconectam depois que `broker.shutdown()` executou os handlers de
  `WORKER_SHUTDOWN`, os middlewares e o backend de resultados. Um `connect()` que falha faz
  `broker.startup()` falhar, e com ele o worker. `Shutdown` e `BackgroundTasks` se comportam como no
  [FastAPI](fastapi.md).
- **Instâncias.** Assim como em `inject()`, um `Client` é uma instância por container, e um
  `NotSingletonClient` é uma instância por argumento que o declara, não uma por tarefa.
- **A função continua sendo uma função.** A assinatura dela mostra
  `Annotated[UserService, TaskiqDepends(...)]` para o taskiq, como no [FastAPI](fastapi.md);
  `await send_report(1, users)` a chama com clientes passados à mão.
- **Um container se conecta uma vez.** Um `InMemoryBroker` iniciado em um processo cujo container já está
  conectado, por exemplo dentro de uma aplicação FastAPI que roda sobre o mesmo `DI`, falha com
  `RuntimeError: nuke-di clients failed to start: the container is already connected`. Dê a esse broker um
  container próprio: `setup(broker, container=Dependencies())`. Uma função de tarefa serve a um broker por
  vez, como um subscriber do FastStream.

**Testes.** Rode as tarefas em um `InMemoryBroker` e inicie-o dentro do override:

```python
# tests/test_tasks.py
import pytest

from app.clients import Database
from app.tasks import broker, send_report
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_send_report(capsys: pytest.CaptureFixture[str]) -> None:
    with DI.override(Database, FakeDatabase()):
        await broker.startup()
        task = await send_report.kiq(1)
        await task.wait_result()
        await broker.shutdown()

    assert "Hello, alice!" in capsys.readouterr().out
```

```console
$ pytest -q tests/test_tasks.py
.                                                                        [100%]
1 passed in 0.38s
```

Uma tarefa executada sem a inicialização do worker, por exemplo enfileirada em um `InMemoryBroker` que nunca
foi iniciado, falha com ``RuntimeError: UserService is not connected: the clients connect when the worker
starts; run tasks with `taskiq worker`, or start an InMemoryBroker with `await broker.startup()` before kicking
them`` no seu resultado. Uma tarefa declarada depois que o worker iniciou falha com
`RuntimeError: UserService was not started with the worker`.
