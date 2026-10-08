# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](#development)
[![License](https://img.shields.io/pypi/l/nuke-di)](../../LICENSE)

[English](https://github.com/troyan-dy/nuke-di/blob/master/README.md) · [Русский](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ru.md) · [简体中文](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.zh-CN.md) · [Español](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.es.md) · **Português (Brasil)** · [日本語](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ja.md) · [Polski](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pl.md)

A injeção de dependências mais simples para projetos Python assíncronos.

As dependências são declaradas com type hints comuns. O `nuke-di` monta a árvore de dependências,
cria cada cliente uma única vez e cuida do seu ciclo de vida assíncrono: `connect()` na inicialização e
`disconnect()` no encerramento. Clientes independentes sobem em paralelo, camada por camada,
das dependências mais profundas para cima.

Além disso, um único decorador transforma uma função assíncrona em um processo: um **job**, que roda
uma vez, ou um **worker**, que roda até ser parado, com parâmetros de linha de comando, encerramento
gracioso no SIGTERM e códigos de saída que significam alguma coisa. Os handlers do FastAPI, do Litestar e do
FastStream recebem clientes pelo type hint da mesma forma.

A biblioteca foi extraída da camada de DI de um framework de microsserviços Python usado em produção
e não tem dependências em tempo de execução.

- [Instalação](#installation)
- [Início rápido](#quick-start)
- [Clientes](#clients): [singletons](#client-and-notsingletonclient), [ciclo de vida](#connect-and-disconnect), [dataclasses](#dataclass-clients), [camadas](#layers), [tempos de inicialização](#startup-timings), [falhas de conexão](#when-a-client-fails-to-connect), [erros de resolução](#when-the-tree-cannot-be-built)
- [O container](#the-container)
- [Workers e jobs](#workers-and-jobs): [um job](#your-first-job), [parâmetros](#parameters), [um worker](#your-first-worker), [período de tolerância](#grace-period), [tarefas em segundo plano](#background-tasks), [códigos de saída](#exit-codes), [hooks](#hooks), [Kubernetes](#running-in-kubernetes)
- Frameworks: [FastAPI](#fastapi), [Litestar](#litestar), [FastStream](#faststream)
- [Testes](#testing)
- [Configuração](#configuration) · [Erros](#errors) · [Desenvolvimento](#development)

## <a id="installation"></a>Instalação

```bash
pip install nuke-di
```

Requer Python 3.11+.

## <a id="quick-start"></a>Início rápido

```python
import asyncio

from nuke_di import DI, Client


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


async def handler(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def main() -> None:
    injected = DI.inject(handler)  # resolves UserService -> Database

    async with DI:  # connect() every client, disconnect() on exit
        print(await injected(42))


asyncio.run(main())
```

```text
database: connected
Hello, user-42!
database: disconnected
```

O que aconteceu:

1. `DI.inject(handler)` leu os type hints de `handler`, encontrou o cliente `UserService`, viu
   que ele precisa de um `Database` no seu `__init__` e construiu os dois. `user_id: int` não é
   um cliente, então continua sendo um argumento comum.
2. `async with DI` chamou `connect()` em cada cliente que construiu, começando pelas dependências.
3. `injected(42)` chamou `handler(42, users=<UserService>)`.
4. Ao sair do bloco `async with`, `disconnect()` foi chamado na ordem inversa.

## <a id="clients"></a>Clientes

### <a id="client-and-notsingletonclient"></a>Client e NotSingletonClient

Toda dependência é uma subclasse de uma destas duas classes base:

| Classe base          | Instâncias                                                  |
|----------------------|-------------------------------------------------------------|
| `Client`             | Singleton: uma instância por container                      |
| `NotSingletonClient` | Uma instância nova para cada consumidor que a declara       |

```python
from nuke_di import Client, Dependencies, NotSingletonClient


class Settings(Client):
    pass


class HttpSession(NotSingletonClient):
    pass


class Orders(Client):
    def __init__(self, settings: Settings, http: HttpSession) -> None:
        self.settings = settings
        self.http = http


class Payments(Client):
    def __init__(self, settings: Settings, http: HttpSession) -> None:
        self.settings = settings
        self.http = http


deps = Dependencies()
orders = deps.resolve(Orders)
payments = deps.resolve(Payments)

print(orders.settings is payments.settings)  # one Settings for the whole container
print(orders.http is payments.http)  # every consumer gets its own HttpSession
print(deps.resolve(Orders) is orders)  # resolve() is idempotent for a Client
```

```text
True
False
True
```

Um cliente declara as próprias dependências como argumentos anotados do `__init__`. Só são injetados
os argumentos anotados com um tipo de cliente, e a resolução é recursiva.

### <a id="connect-and-disconnect"></a>connect() e disconnect()

Sobrescreva os métodos assíncronos `connect()` / `disconnect()` para abrir e liberar recursos, como
pools de conexões. O `__init__` apenas guarda as dependências; tudo o que faz I/O deve ficar
em `connect()`:

```python
class Redis(Client):
    def __init__(self) -> None:
        self._pool: Pool | None = None

    async def connect(self) -> None:
        self._pool = await create_pool()

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
```

Cada `connect()` é limitado por `CONNECT_TIMEOUT_SECONDS` (padrão `30`) e cada
`disconnect()` por `DISCONNECT_TIMEOUT_SECONDS` (padrão `10`). Um `disconnect()` que falha ou
trava é registrado no log, e os demais clientes são encerrados mesmo assim.

### <a id="dataclass-clients"></a>Clientes dataclass

`client_dataclass` transforma uma classe em `Client` e em dataclass ao mesmo tempo, de modo que os campos
passam a ser as dependências injetadas:

```python
from nuke_di import Client, Dependencies, client_dataclass


class Postgres(Client):
    pass


class Payments(Client):
    pass


@client_dataclass(frozen=True)
class Checkout:
    pg: Postgres
    payments: Payments


checkout = Dependencies().resolve(Checkout)
print(checkout)
print(isinstance(checkout, Client))
```

```text
Checkout(pg=<__main__.Postgres object at 0x...>, payments=<__main__.Payments object at 0x...>)
True
```

Aceita os mesmos argumentos nomeados que `dataclasses.dataclass`.

### <a id="layers"></a>Camadas

Os clientes se conectam em paralelo, por camadas. Clientes sem dependências formam a camada 0;
todos os outros ficam uma camada acima da sua dependência mais alta. Uma camada só começa
depois que a anterior terminou de se conectar, então um cliente nunca se conecta antes das suas
próprias dependências. `disconnect()` percorre as camadas na ordem inversa.

```python
import asyncio
import logging

from nuke_di import Client, Dependencies

logging.basicConfig(level=logging.DEBUG, format="%(message)s")
logging.getLogger("asyncio").setLevel(logging.WARNING)  # keep only the nuke_di records


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.2)
        print("  postgres ready")


class Redis(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        print("  redis ready")


class Payments(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Checkout(Client):
    def __init__(self, pg: Postgres, redis: Redis, payments: Payments) -> None:
        self.pg, self.redis, self.payments = pg, redis, payments


async def main() -> None:
    deps = Dependencies()
    deps.resolve(Checkout)
    async with deps:
        print("-- application is running --")


asyncio.run(main())
```

O log `DEBUG` do logger `nuke_di` mostra as camadas:

```text
Resolving dependency "Checkout"
Resolving dependency "Postgres"
Resolving dependency "Redis"
Resolving dependency "Payments"
Connecting layer 0: Postgres, Redis
Connecting client Postgres
Connecting client Redis
  redis ready
Connected client Redis in 0.101s
  postgres ready
Connected client Postgres in 0.201s
Connecting layer 1: Payments
Connecting client Payments
Connected client Payments in 0.000s
Connecting layer 2: Checkout
Connecting client Checkout
Connected client Checkout in 0.000s
Connected 4 clients in 3 layers in 0.20s (slowest: Postgres 0.20s, Redis 0.10s, Payments 0.00s)
-- application is running --
Disconnecting client Checkout
Disconnected client Checkout in 0.000s
Disconnecting client Payments
Disconnected client Payments in 0.000s
Disconnecting client Postgres
Disconnected client Postgres in 0.000s
Disconnecting client Redis
Disconnected client Redis in 0.000s
```

```text
Checkout(pg, redis, payments)    layer 2
Payments(pg)                     layer 1
Postgres, Redis                  layer 0  <- connect together, in 0.2s rather than 0.3s
```

Só as dependências declaradas no `__init__` entram na ordenação. Se um cliente precisa que outro
já esteja conectado, declare-o como dependência. Defina `CONNECT_CONCURRENCY` para limitar quantos
clientes se conectam ao mesmo tempo.

### <a id="startup-timings"></a>Tempos de inicialização

O contêiner mede o `connect()` e o `disconnect()` de cada cliente, então uma inicialização
lenta aponta o culpado. Depois de um `connect()` bem-sucedido ele registra um resumo em
`INFO` e um `WARNING` para cada cliente que usou mais da metade de `CONNECT_TIMEOUT_SECONDS`,
bem antes de esse cliente começar a falhar por timeout:

```python
# startup.py
import asyncio
import logging

from nuke_di import Client, Dependencies, DependenciesSettings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.2)


class Kafka(Client):
    async def connect(self) -> None:
        await asyncio.sleep(1.6)

    async def disconnect(self) -> None:
        await asyncio.sleep(0.3)


class Orders(Client):
    def __init__(self, pg: Postgres, kafka: Kafka) -> None:
        self.pg, self.kafka = pg, kafka


async def main() -> None:
    deps = Dependencies(settings=DependenciesSettings(connect_timeout=3))
    deps.resolve(Orders)
    async with deps:
        print("-- application is running --")

    for t in deps.timings:
        print(
            f"{t.name:<8} layer {t.layer}  connect {t.connect:.2f}s {t.connect_outcome:<3}  "
            f"disconnect {t.disconnect:.2f}s {t.disconnect_outcome}"
        )


asyncio.run(main())
```

```console
$ python startup.py
INFO Connected 3 clients in 2 layers in 1.60s (slowest: Kafka 1.60s, Postgres 0.20s, Orders 0.00s)
WARNING Client Kafka took 1.60s to connect, more than half of CONNECT_TIMEOUT_SECONDS (3s)
-- application is running --
Postgres layer 0  connect 0.20s ok   disconnect 0.00s ok
Kafka    layer 0  connect 1.60s ok   disconnect 0.30s ok
Orders   layer 1  connect 0.00s ok   disconnect 0.00s ok
```

`deps.timings` guarda um `ClientTiming` por cliente do último `connect()`, na ordem de
conexão. Ele sobrevive ao `disconnect()`, então pode ser lido depois que o contêiner parou.
Em um app FastAPI, o lifespan que você passa para `FastAPI()` roda dentro do contêiner
conectado, então ele vê os tempos de conexão. Um worker ou um job recebe a mesma lista em
[`Run.clients`](#startup-metrics-and-structured-logs).

| Campo de `ClientTiming` | Valor |
|-------------------------|-------|
| `name`               | O nome da classe do cliente |
| `layer`              | A [camada](#layers) do cliente |
| `connect`            | Segundos dentro de `connect()`, sem contar a espera por `CONNECT_CONCURRENCY`; `None` se `connect()` nunca rodou |
| `connect_outcome`    | `"ok"`, `"failed"`, `"timed_out"`, `"cancelled"`, ou `None` se `connect()` nunca começou |
| `disconnect`, `disconnect_outcome` | O mesmo para `disconnect()`; `None` até o cliente se desconectar |

Quando um cliente não consegue se conectar, os clientes da camada dele que ainda estão se
conectando ficam como `"cancelled"`, as camadas acima mantêm `None` e os clientes que já
tinham se conectado são revertidos, então recebem um `disconnect_outcome`. A biblioteca só
mede: exportar os tempos como métricas ou spans fica a cargo do seu código.

### <a id="when-a-client-fails-to-connect"></a>Quando um cliente não consegue se conectar

Se um cliente não consegue se conectar, o restante da sua camada é cancelado e as camadas seguintes
nem começam. Os clientes que já estavam conectados são desconectados, camada por camada na ordem inversa, e o
container fica desconectado e vazio:

```python
import asyncio

from nuke_di import Client, ConnectError, Dependencies


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")


class Kafka(Client):
    async def connect(self) -> None:
        raise OSError("broker kafka-1:9092 is unreachable")


class Orders(Client):
    def __init__(self, pg: Postgres, kafka: Kafka) -> None:
        self.pg, self.kafka = pg, kafka


async def main() -> None:
    deps = Dependencies()
    deps.resolve(Orders)
    try:
        await deps.connect()
    except ConnectError as exc:
        print(f"{exc} <- {exc.__cause__!r}")
    print("connected:", deps.connected)


asyncio.run(main())
```

```text
postgres: connected
Error occurred connecting client Kafka
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
postgres: disconnected
Error occurred connecting client Kafka <- OSError('broker kafka-1:9092 is unreachable')
connected: False
```

A mesma limpeza acontece quando o próprio `connect()` é cancelado. `ConnectError` herda de
`SystemExit`, então uma aplicação que não a captura é encerrada, o que geralmente é o desejado
quando uma dependência está fora do ar. Clientes mockados não são conectados e não afetam as camadas.

### <a id="when-the-tree-cannot-be-built"></a>Quando a árvore não pode ser construída

A resolução verifica cada `__init__` antes de chamá-lo, então um cliente que não pode ser construído falha
antes de qualquer conexão, com o nome do argumento e o caminho a partir do cliente que você pediu:

```python
from typing import Protocol

from nuke_di import Client, Dependencies, InvalidSignatureError


class Postgres(Client):
    pass


class UserRepository(Protocol):
    async def get(self, user_id: int) -> str: ...


class Profiles(Client):
    def __init__(self, pg: Postgres, users: UserRepository) -> None:
        self.pg, self.users = pg, users


class Checkout(Client):
    def __init__(self, profiles: Profiles) -> None:
        self.profiles = profiles


class Orders(Client):
    def __init__(self, payments: "Payments") -> None:
        self.payments = payments


class Payments(Client):
    def __init__(self, orders: Orders) -> None:
        self.orders = orders


for root in (Checkout, Orders):
    try:
        Dependencies().resolve(root)
    except InvalidSignatureError as exc:
        print(f"{type(exc).__name__}: {exc}")
```

```text
InvalidSignatureError: Argument "users" of "Profiles.__init__" is UserRepository, which is not a client (resolving Checkout -> Profiles)
CircularDependencyError: Circular dependency: Orders -> Payments -> Orders
```

Um argumento do `__init__` recebe um cliente quando o seu type hint é um cliente. Qualquer outro
argumento precisa de um valor padrão, que é mantido como está. Estes casos falham com `InvalidSignatureError`:

| Argumento do `__init__` sem valor padrão | Mensagem                                          |
|------------------------------------------|---------------------------------------------------|
| sem type hint                            | `has no type hint`                                |
| um tipo que não é cliente                | `is UserRepository, which is not a client`        |
| `Client \| None`                         | `is Postgres \| None, a client cannot be optional` |
| um cliente, positional-only (`/`)        | `is positional-only, a client is passed by keyword` |

Clientes que dependem uns dos outros em ciclo falham com `CircularDependencyError`, uma subclasse de
`InvalidSignatureError`, e um type hint que não pode ser avaliado, por exemplo uma classe definida dentro de uma
função ou importada sob `TYPE_CHECKING`, falha com um `InvalidSignatureError` que explica isso. Quando o
erro vem de `inject()`, o caminho começa na função:
`(resolving handler -> Checkout -> Profiles)`. Em um [worker ou job](#workers-and-jobs),
qualquer um desses erros faz a execução falhar com código de saída `1` antes de qualquer conexão.

## <a id="the-container"></a>O container

`Dependencies` é o container. `DI` é uma instância global pronta para uso; crie a sua
quando precisar de isolamento, por exemplo nos testes.

| Método               | Descrição                                                               |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Constrói `cls` e a sua árvore de dependências. Idempotente para `Client`. |
| `inject(func)`       | Retorna `functools.partial(func, ...)` com os argumentos de cliente já vinculados. Todo argumento de `func`, exceto `*args` / `**kwargs`, precisa ter type hint. |
| `connect()`          | Chama `connect()` em cada cliente resolvido, camada por camada.         |
| `disconnect()`       | Chama `disconnect()` camada por camada na ordem inversa e depois faz `flush()` do container. |
| `async with`         | `connect()` na entrada, `disconnect()` na saída.                        |
| `mock(cls, new=None)`| Registra um substituto para `cls` (por padrão, um mock com autospec) até o próximo `flush()`. Precisa vir antes de `cls` ser resolvido. |
| `override(cls, new=None)` | Um substituto que vale durante um bloco `with`, seguido de `flush()`; veja [Testes](#testing). |
| `flush()`            | Esquece todos os clientes resolvidos.                                   |
| `timings`            | Um `ClientTiming` por cliente do último `connect()`; veja [Tempos de inicialização](#startup-timings). |

`resolve`, `inject`, `mock`, `override` e `flush` só funcionam enquanto o container está desconectado:
a árvore inteira é construída antes da inicialização.

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: already connected
```

## <a id="workers-and-jobs"></a>Workers e jobs

Com um único decorador, uma função assíncrona vira o programa principal de um processo:

| Decorador | Roda                                                   |
|-----------|--------------------------------------------------------|
| `@job`    | Uma vez: o processo termina quando a função retorna    |
| `@worker` | Até o processo receber SIGTERM ou SIGINT               |

Os exemplos desta seção compartilham um mesmo módulo de clientes:

```python
# app/clients.py
import datetime
import itertools

from nuke_di import Client


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")

    async def upsert(self, table: str, rows: list[str]) -> None:
        print(f"postgres: upserted {len(rows)} rows into {table}")


class Warehouse(Client):
    async def connect(self) -> None:
        print("warehouse: connected")

    async def disconnect(self) -> None:
        print("warehouse: disconnected")

    async def changes(self, table: str, day: datetime.date) -> list[str]:
        return [f"{table}:{day}:{n}" for n in range(3)]


class Queue(Client):
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    async def connect(self) -> None:
        print("queue: connected")

    async def disconnect(self) -> None:
        print("queue: disconnected")

    async def get(self) -> str:
        return f"message-{next(self._ids)}"
```

### <a id="your-first-job"></a>Seu primeiro job

```python
# app/jobs/sync.py
import datetime

from nuke_di import job

from app.clients import Postgres, Warehouse


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None:
    day = datetime.date.today() - datetime.timedelta(days=1)
    for table in ["users", "orders"]:
        await pg.upsert(table, await warehouse.changes(table, day))
```

```console
$ python -m app.jobs.sync
postgres: connected
warehouse: connected
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
$ echo $?
0
```

Esse é o programa inteiro: sem `main()`, sem `asyncio.run()`, sem `if __name__ == "__main__"`.
O processo resolve os clientes a partir do container global `DI`, conecta-os, executa a
função, desconecta-os e termina com um [código de saída](#exit-codes). O agendamento não faz parte
da biblioteca: um CronJob do Kubernetes, um timer do systemd ou o crontab decide quando um job roda.

O `nuke-di` registra cada execução no logger `nuke_di`. Configure o logging acima do decorador para
vê-la:

```python
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s: %(message)s")


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None: ...
```

```console
$ python -m app.jobs.sync
INFO  nuke_di.run: Starting job app.jobs.sync.sync
postgres: connected
warehouse: connected
INFO  nuke_di.core: Connected 4 clients in 1 layer in 0.00s (slowest: Warehouse 0.00s, Postgres 0.00s, Shutdown 0.00s)
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
INFO  nuke_di.run: Run app.jobs.sync.sync finished with exit code 0 in 0.002s
```

#### <a id="one-entrypoint-per-module-defined-last"></a>Um ponto de entrada por módulo, definido por último

Quando o módulo é executado como `__main__`, o decorador executa a função imediatamente e o
processo termina ali mesmo:

```python
# app/jobs/sync.py
DI.mock(Warehouse, FakeWarehouse())  # runs: code above the decorator is fine


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None: ...


print("never printed")  # never runs under `python -m app.jobs.sync`
```

**Mantenha um ponto de entrada por módulo e defina-o por último.** Em um import normal, por exemplo a partir de um teste,
o decorador retorna a função inalterada e nada é executado. A função decorada precisa ser
declarada com `async def`; caso contrário, um `TypeError` é lançado no import.

### <a id="parameters"></a>Parâmetros

Todo argumento anotado que não é um cliente vira uma opção de linha de comando. Aqui está o mesmo
job, agora capaz de copiar qualquer dia, só algumas tabelas, em modo de simulação (dry run):

```python
# app/jobs/sync.py
import datetime
import enum
from typing import Annotated

from nuke_di import Option, job

from app.clients import Postgres, Warehouse


class Mode(enum.Enum):
    INCREMENTAL = "incremental"
    FULL = "full"


@job
async def sync(
    pg: Postgres,
    warehouse: Warehouse,
    day: Annotated[datetime.date, Option(help="Day to copy, YYYY-MM-DD", short="d")],
    tables: Annotated[
        list[str] | None, Option(help="Table to copy, repeat for several; all by default", short="t")
    ] = None,
    mode: Mode = Mode.INCREMENTAL,
    dry_run: Annotated[bool, Option(help="Read the changes, write nothing")] = False,
) -> None:
    """Copy one day of changes from the warehouse into Postgres."""
    print(f"sync: {mode.name} copy of {day}")
    for table in tables or ["users", "orders"]:
        rows = await warehouse.changes(table, day)
        if dry_run:
            print(f"sync: would upsert {len(rows)} rows into {table}")
        else:
            await pg.upsert(table, rows)
```

`pg` e `warehouse` são clientes e são injetados; `day`, `tables`, `mode` e `dry_run` vêm
da linha de comando:

```console
$ python -m app.jobs.sync --day 2026-10-01
postgres: connected
warehouse: connected
sync: INCREMENTAL copy of 2026-10-01
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected

$ python -m app.jobs.sync -d 2026-10-01 -t users --mode FULL --dry-run
postgres: connected
warehouse: connected
sync: FULL copy of 2026-10-01
sync: would upsert 3 rows into users
postgres: disconnected
warehouse: disconnected
```

O `--help` é gerado a partir da assinatura e da docstring. Ele não conecta nada
(no Python 3.13+ aparece `-d, --day DAY` em vez de `-d DAY, --day DAY`):

```console
$ python -m app.jobs.sync --help
usage: python -m app.jobs.sync [-h] -d DAY [-t TABLES]
                               [--mode {INCREMENTAL,FULL}]
                               [--dry-run | --no-dry-run]

Copy one day of changes from the warehouse into Postgres.

options:
  -h, --help            show this help message and exit
  -d DAY, --day DAY     Day to copy, YYYY-MM-DD
  -t TABLES, --tables TABLES
                        Table to copy, repeat for several; all by default
  --mode {INCREMENTAL,FULL}
                        (default: INCREMENTAL)
  --dry-run, --no-dry-run
                        Read the changes, write nothing (default: False)
```

Uma linha de comando inválida é rejeitada **antes que qualquer cliente seja resolvido ou conectado**, com código de
saída `2`:

```console
$ python -m app.jobs.sync
usage: python -m app.jobs.sync [-h] -d DAY [-t TABLES]
                               [--mode {INCREMENTAL,FULL}]
                               [--dry-run | --no-dry-run]
python -m app.jobs.sync: error: the following arguments are required: -d/--day
Run app.jobs.sync.sync failed: the following arguments are required: -d/--day
$ echo $?
2

$ python -m app.jobs.sync --day yesterday
...
python -m app.jobs.sync: error: argument -d/--day: invalid date value: 'yesterday'

$ python -m app.jobs.sync -d 2026-10-01 --mode full
...
python -m app.jobs.sync: error: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)

$ python -m app.jobs.sync -d 2026-10-01 --dry
...
python -m app.jobs.sync: error: unrecognized arguments: --dry
```

As duas primeiras linhas de cada erro são impressas pelo `argparse`; a linha `Run ... failed` é o
registro `ERROR` do logger `nuke_di`, então ela segue a sua configuração de logging.
Abreviações não são aceitas: `--dry` não é interpretado como `--dry-run`.

#### <a id="supported-types"></a>Tipos suportados

| Anotação                                      | Linha de comando                | Exemplo                         |
|-----------------------------------------------|---------------------------------|---------------------------------|
| `str`, `int`, `float`, `pathlib.Path`         | `--name VALUE`                  | `--limit 10`                    |
| `bool`                                        | `--name` / `--no-name`          | `--dry-run`                     |
| `datetime.date`, `datetime.datetime`          | ISO 8601                        | `--since 2026-10-01T12:00:00`   |
| um `Enum`                                     | o **nome** do membro, exatamente como está escrito | `--mode FULL`  |
| `list[T]` de qualquer um dos tipos acima, exceto `bool` | a opção repetida      | `--table users --table orders`  |
| `T \| None`                                   | como `T`                        | `--limit 10`                    |

As regras:

- **O nome.** A opção recebe o nome do argumento, com `_` trocado por `-`:
  `dry_run` vira `--dry-run`. Não há argumentos posicionais, então adicionar um parâmetro nunca
  quebra uma linha de comando existente.
- **Obrigatório ou não.** Um argumento sem valor padrão é uma opção obrigatória. Um argumento com
  valor padrão é opcional e, quando a opção é omitida, vale o padrão da própria função.
- **`Option`.** `Annotated[T, Option(help=..., short=...)]` adiciona um texto de ajuda e um
  alias de uma letra, como `-d`. Ambos são opcionais.
- **Sem parâmetros.** Um ponto de entrada sem parâmetros também faz o parsing da linha de comando: ele
  responde a `--help` e rejeita qualquer argumento com código de saída `2`.

Estas assinaturas são bugs no código, e não na linha de comando. Elas fazem a execução falhar com
`InvalidSignatureError` e código de saída `1`:

```python
async def sync(day: dict[str, int]) -> None: ...  # unsupported type
async def sync(pg: Annotated[Postgres, Option(help="...")]) -> None: ...  # Option on a client
async def sync(help: bool = False) -> None: ...  # clashes with --help
async def sync(day: int, /) -> None: ...  # positional-only
```

#### <a id="parameters-in-tests"></a>Parâmetros nos testes

A função decorada continua sendo uma corrotina comum, então um teste passa os parâmetros como argumentos
nomeados:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    warehouse.changes.assert_awaited_once_with("users", datetime.date(2026, 10, 1))
    pg.upsert.assert_awaited_once_with("users", ["row"])
```

### <a id="your-first-worker"></a>Seu primeiro worker

Um worker roda até que o processo receba um pedido para parar. Ele depende do cliente `Shutdown`, que
é acionado no primeiro SIGTERM ou SIGINT, e termina a unidade de trabalho em andamento:

```python
# app/workers/consumer.py
import asyncio

from nuke_di import Shutdown, worker

from app.clients import Queue


@worker
async def consumer(queue: Queue, shutdown: Shutdown) -> None:
    while not shutdown.is_set():
        message = await queue.get()
        print(f"consumer: processing {message}")
        await asyncio.sleep(1)  # the actual work
        print(f"consumer: done {message}")
    print("consumer: stopped")
```

Ctrl+C no meio da terceira mensagem: a mensagem é concluída, o loop termina e os
clientes se desconectam.

```console
$ python -m app.workers.consumer
queue: connected
consumer: processing message-1
consumer: done message-1
consumer: processing message-2
consumer: done message-2
consumer: processing message-3
^C
consumer: done message-3
consumer: stopped
queue: disconnected
$ echo $?
130
```

`Shutdown` tem três métodos:

| Método           | Descrição                                                                 |
|------------------|---------------------------------------------------------------------------|
| `is_set()`       | Indica se o Shutdown já começou; verifique entre uma unidade de trabalho e outra |
| `await wait()`   | Bloqueia até o Shutdown começar                                           |
| `set()`          | Inicia o Shutdown manualmente, por exemplo em um teste                    |

Fora de um worker ou job, nada o aciona, então um loop que depende de `Shutdown` também funciona
sem mudanças dentro de uma aplicação web. Um worker que retorna ou lança uma exceção por conta própria também encerra o
processo: reiniciá-lo é tarefa do orquestrador.

No Windows, só o SIGINT (Ctrl+C) é tratado; o SIGTERM mantém o comportamento padrão.

### <a id="grace-period"></a>Período de tolerância

Um worker que ignora o `Shutdown` é cancelado depois de `SHUTDOWN_GRACE_SECONDS` (padrão `10`):

```python
# app/workers/stubborn.py
@worker
async def stubborn(queue: Queue) -> None:
    while True:  # never looks at Shutdown
        message = await queue.get()
        print(f"stubborn: processing {message}")
        await asyncio.sleep(5)
```

```console
$ SHUTDOWN_GRACE_SECONDS=2 python -m app.workers.stubborn &
queue: connected
stubborn: processing message-1
$ kill -TERM %1
Run app.workers.stubborn.stubborn did not stop within 2.0s after Shutdown, cancelling it
queue: disconnected
$ wait %1; echo $?
143
```

Um segundo sinal cancela o ponto de entrada imediatamente, sem esperar o período de tolerância, por exemplo
com Ctrl+C duas vezes:

```console
$ python -m app.workers.stubborn
queue: connected
stubborn: processing message-1
^C^C
Second SIGINT, cancelling run app.workers.stubborn.stubborn
queue: disconnected
```

Um sinal que chega enquanto os clientes ainda estão se conectando interrompe a inicialização, e os
clientes que já se conectaram são desconectados.

No pior caso, um processo para em
`SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers`. Com os valores padrão, uma árvore de
duas camadas consome todo o `terminationGracePeriodSeconds` padrão do Kubernetes, de 30 segundos,
então reduza os timeouts ou aumente o período de tolerância para árvores mais profundas.

### <a id="background-tasks"></a>Tarefas em segundo plano

`BackgroundTasks` é um cliente que supervisiona corrotinas que rodam ao lado do ponto de entrada.
Ao contrário de um `asyncio.create_task()` solto, uma tarefa que falha nunca se perde: ela é registrada no log com o
traceback e derruba o processo inteiro.

```python
# app/workers/indexer.py
import asyncio

from nuke_di import BackgroundTasks, Shutdown, worker

from app.clients import Queue


async def refresh_index() -> None:
    for attempt in range(1, 10):
        print(f"refresh: run {attempt}")
        await asyncio.sleep(0.5)
        if attempt == 2:
            raise ConnectionError("search cluster is unreachable")


@worker
async def indexer(queue: Queue, tasks: BackgroundTasks, shutdown: Shutdown) -> None:
    tasks.spawn(refresh_index(), name="refresh-index")
    print("indexer: waiting for Shutdown")
    await shutdown.wait()
```

```console
$ python -m app.workers.indexer
queue: connected
indexer: waiting for Shutdown
refresh: run 1
refresh: run 2
Background task refresh-index failed
Traceback (most recent call last):
  ...
ConnectionError: search cluster is unreachable
queue: disconnected
Run app.workers.indexer.indexer failed
Traceback (most recent call last):
  ...
ConnectionError: search cluster is unreachable
$ echo $?
1
```

O worker foi cancelado sem período de tolerância: um loop em segundo plano que quebrou não pode deixar para trás um
processo vivo que não faz nada. Quando o processo para, por qualquer motivo, as tarefas são canceladas
e aguardadas **antes** que qualquer cliente se desconecte, então elas nunca rodam com clientes já fechados.

| Método                     | Descrição                                                       |
|----------------------------|-----------------------------------------------------------------|
| `spawn(coro, name=None)`   | Inicia `coro` como tarefa e mantém uma referência a ela até que termine |
| `watch(callback)`          | Chama `callback(exc)` para cada tarefa que falhar               |
| `await stop()`             | Cancela todas as tarefas e espera todas terminarem; é chamado por `disconnect()` |

Fora de um worker ou job, por exemplo sob um simples `async with DI`, as falhas são apenas registradas no log e
as tarefas são canceladas no `disconnect()`.

### <a id="exit-codes"></a>Códigos de saída

Vale a primeira regra que se aplicar:

| Condição                                                                                            | Código de saída |
|-----------------------------------------------------------------------------------------------------|-----------------|
| Linha de comando inválida (`UsageError`)                                                            | `2`             |
| Uma exceção: na assinatura, na resolução ou conexão dos clientes, no ponto de entrada, em uma tarefa em segundo plano | `1` |
| Foi recebido um sinal de término                                                                    | `128 + signum`  |
| Nos demais casos                                                                                    | `0`             |

O SIGTERM resulta em `143` e o SIGINT em `130`. Um job que percebe o Shutdown e retorna normalmente
ainda assim termina com `128 + signum`: o trabalho foi interrompido, e um agendador não deve considerá-lo
concluído.

Os códigos servem para quem quer que inicie o processo:

```bash
python -m app.jobs.sync --day 2026-10-01
case $? in
  0)       echo "synced" ;;
  2)       echo "fix the command line, retrying will not help" ;;
  130|143) echo "interrupted, safe to run again" ;;
  *)       echo "failed, see the log" ;;
esac
```

### <a id="hooks"></a>Hooks

Os hooks observam cada execução, por exemplo para enviar métricas ou abrir um span de tracing:

```python
# app/jobs/report.py
from nuke_di import Run, job

from app.clients import Postgres


class Timer:
    async def on_start(self, run: Run) -> None:
        print(f"hook: {run.kind} {run.name} started")

    async def on_finish(self, run: Run) -> None:
        seconds = (run.finished_at - run.started_at).total_seconds()
        print(f"hook: exit code {run.exit_code} in {seconds:.1f}s, error: {run.error!r}")


@job(hooks=[Timer()])
async def report(pg: Postgres, limit: int = 10) -> None:
    print(f"report: top {limit} customers")
```

```console
$ python -m app.jobs.report --limit 3
hook: job app.jobs.report.report started
postgres: connected
report: top 3 customers
postgres: disconnected
hook: exit code 0 in 0.0s, error: None

$ python -m app.jobs.report --limit three
usage: python -m app.jobs.report [-h] [--limit LIMIT]
python -m app.jobs.report: error: argument --limit: invalid int value: 'three'
hook: job app.jobs.report.report started
Run app.jobs.report.report failed: argument --limit: invalid int value: 'three'
hook: exit code 2 in 0.0s, error: UsageError("argument --limit: invalid int value: 'three'")
```

`on_start` é chamado na ordem da lista, antes de os clientes serem resolvidos; `on_finish`, na ordem
inversa, depois que eles se desconectaram, de modo que vê o estado final do `Run`, incluindo falhas
de conexão:

| Campo de `Run` | Valor                                                                  |
|----------------|------------------------------------------------------------------------|
| `name`         | Módulo e função, por exemplo `app.jobs.report.report`                  |
| `kind`         | `"job"` ou `"worker"`                                                  |
| `started_at`   | `datetime` em UTC                                                      |
| `finished_at`  | `datetime` em UTC, preenchido antes de `on_finish`                     |
| `exit_code`    | O código de saída do processo, preenchido antes de `on_finish`         |
| `error`        | A exceção que fez a execução falhar, por exemplo um `UsageError`, ou `None` |
| `signal`       | O primeiro sinal de término recebido, ou `None`                        |
| `clients`      | Um `ClientTiming` por cliente: durações e resultados de conexão e desconexão; vazio se a execução falhou antes de conectar |

Hooks são objetos comuns, não clientes: eles gerenciam os próprios recursos. Uma exceção em um hook
é registrada no log e não altera o código de saída. `--help` não é uma execução, então os hooks não o veem.

#### <a id="startup-metrics-and-structured-logs"></a>Métricas de inicialização e logs estruturados

`run.clients` é o lugar para exportar métricas de inicialização: `on_finish` vê quanto tempo
cada cliente levou para se conectar e se desconectar. Além disso, cada registro de log do
`nuke_di` carrega campos estruturados, então um formatador JSON pode filtrar e agregar por
cliente sem analisar as mensagens:

```python
# app/jobs/startup.py
import json
import logging

from nuke_di import Run, job

from app.clients import Postgres, Warehouse


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {key: getattr(record, key) for key in ("run", "client", "layer", "duration") if hasattr(record, key)}
        return json.dumps({"level": record.levelname, "message": record.getMessage(), **fields})


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])


class StartupMetrics:
    async def on_start(self, run: Run) -> None:
        pass

    async def on_finish(self, run: Run) -> None:
        for client in run.clients:
            print(f"metric: {client.name} connect={client.connect:.3f}s {client.connect_outcome}")


@job(hooks=[StartupMetrics()])
async def startup(pg: Postgres, warehouse: Warehouse) -> None:
    print("startup: done")
```

```console
$ python -m app.jobs.startup
{"level": "INFO", "message": "Starting job app.jobs.startup.startup", "run": "app.jobs.startup.startup"}
postgres: connected
warehouse: connected
{"level": "INFO", "message": "Connected 4 clients in 1 layer in 0.00s (slowest: Postgres 0.00s, Shutdown 0.00s, Warehouse 0.00s)", "run": "app.jobs.startup.startup", "duration": 0.00015945796621963382}
startup: done
postgres: disconnected
warehouse: disconnected
{"level": "INFO", "message": "Run app.jobs.startup.startup finished with exit code 0 in 0.001s", "run": "app.jobs.startup.startup", "duration": 0.001171}
metric: Shutdown connect=0.000s ok
metric: BackgroundTasks connect=0.000s ok
metric: Postgres connect=0.000s ok
metric: Warehouse connect=0.000s ok
```

| Campo      | Presente em                                                                   |
|------------|-------------------------------------------------------------------------------|
| `run`      | Cada registro feito dentro de um worker ou um job, incluindo os do contêiner: o nome da execução |
| `client`   | Cada registro sobre um cliente: resolução, conexão, desconexão, falhas        |
| `layer`    | Cada registro sobre um cliente que se conecta ou desconecta, e `Connecting layer` |
| `duration` | Segundos: um cliente conectado ou desconectado, o resumo de inicialização, uma execução encerrada |

Cada execução também conecta seus próprios clientes `Shutdown` e `BackgroundTasks`, então eles
aparecem em `run.clients` e no resumo.

### <a id="running-in-kubernetes"></a>Execução no Kubernetes

Um job corresponde a um CronJob, e um worker a um Deployment. Dê ao worker um
`terminationGracePeriodSeconds` suficiente para o [tempo de encerramento](#grace-period):

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: report
spec:
  schedule: "0 6 * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: report
              image: registry.example.com/app:1.0
              command: ["python", "-m", "app.jobs.report"]
              args: ["--limit", "20"]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: consumer
spec:
  replicas: 2
  selector:
    matchLabels: {app: consumer}
  template:
    metadata:
      labels: {app: consumer}
    spec:
      terminationGracePeriodSeconds: 30  # >= SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers
      containers:
        - name: consumer
          image: registry.example.com/app:1.0
          command: ["python", "-m", "app.workers.consumer"]
          env:
            - {name: SHUTDOWN_GRACE_SECONDS, value: "15"}
```

Um backfill pontual usa a mesma imagem com outros parâmetros:

```bash
kubectl run sync-backfill --rm -it --restart=Never --image=registry.example.com/app:1.0 \
  --command -- python -m app.jobs.sync --day 2026-09-30 --mode FULL
```

## <a id="fastapi"></a>FastAPI

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
   dos routers que ela inclui, e os conectou, camada por camada. No encerramento, desconectou-os.
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
inicie a aplicação, com [`override()`](#testing) ou com a fixture `global_di`:

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
Error occurred connecting client Kafka
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
ERROR:    Traceback (most recent call last):
  ...
nuke_di.errors.ConnectError: Error occurred connecting client Kafka

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ...
RuntimeError: nuke-di clients failed to start: Error occurred connecting client Kafka

ERROR:    Application startup failed. Exiting.
$ echo $?
3
```

### <a id="not-supported"></a>Não suportado

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

## <a id="litestar"></a>Litestar

Um route handler do Litestar também recebe um cliente pelo type hint, por meio de um plugin:

```bash
pip install "nuke-di[litestar]"
```

Requer Litestar 2.15 ou mais recente. Com os clientes dos exemplos de [FastAPI](#fastapi):

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
  `BackgroundTasks` se comportam como no [FastAPI](#fastapi).
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

## <a id="faststream"></a>FastStream

Um subscriber do FastStream recebe um cliente pelo type hint, ao lado da mensagem:

```bash
pip install "nuke-di[faststream]"
```

Requer FastStream 0.6 ou mais recente, com qualquer broker. Com os clientes dos exemplos de [FastAPI](#fastapi):

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
  `Shutdown` e `BackgroundTasks` se comportam como no [FastAPI](#fastapi). `setup()` também funciona em um
  `AsgiFastStream`.
- **Instâncias.** Assim como em `inject()`, um `Client` é uma instância por container, e um
  `NotSingletonClient` é uma instância por argumento que o declara, não uma por mensagem.
- **A função continua sendo uma função.** A assinatura dela mostra `Annotated[UserService, Depends(...)]` para o
  FastStream, como no [FastAPI](#fastapi).
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

## <a id="testing"></a>Testes

**Um cliente via container.** Registre os mocks antes de a árvore ser resolvida; a partir daí, todo consumidor
recebe o mock:

```python
from unittest.mock import call

from nuke_di import Dependencies


async def test_greet() -> None:
    deps = Dependencies()
    db = deps.mock(Database)
    db.fetch_user.return_value = "alice"

    users = deps.resolve(UserService)
    async with deps:
        assert await users.greet(1) == "Hello, alice!"

    assert db.fetch_user.await_args_list == [call(1)]
```

**Um cliente para um único bloco, com `override()`.** `override(cls, new=None)` registra um substituto
como `mock()`, mas ele vale até o fim do bloco `with`, mesmo ao longo de vários ciclos de `async with`,
e o container é limpo com `flush()` na saída, então nada que foi resolvido com ele vaza para o próximo teste.
Também funciona com o `DI` global:

```python
# test_greet.py, with Database, UserService and handler from the Quick start
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_greet_with_fake() -> None:
    with DI.override(Database, FakeDatabase()):
        injected = DI.inject(handler)
        async with DI:
            print(await injected(1))

    print("after the block:", DI.clients)


async def test_greet_with_autospec() -> None:
    with DI.override(Database) as db:  # an autospec mock by default
        db.fetch_user.return_value = "bob"
        injected = DI.inject(handler)
        async with DI:
            print(await injected(2))

    db.fetch_user.assert_awaited_once_with(2)
```

Os testes assíncronos desta seção usam [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) com
`asyncio_mode = auto` no `pytest.ini`; sem isso, o pytest não executa testes `async def`.

```console
$ pytest -q -s test_greet.py
Hello, alice!
after the block: OrderedDict()
.Hello, bob!
.
2 passed in 0.01s
```

`database: connected` nunca é impresso: um substituto não é conectado.

As regras:

- **Substitua antes de resolver.** Um substituto registrado depois que `cls` foi resolvido chegaria
  só aos consumidores resolvidos depois, enquanto os anteriores continuariam com o cliente real, por isso `mock()`
  lança uma exceção:

  ```python
  DI.inject(handler)  # resolves UserService -> Database
  DI.mock(Database)  # ConnectError: Database is already resolved, call mock() before resolve() or inject()
  ```

- **`override()` parte de um container sem clientes resolvidos.** Caso contrário, o `flush()` na saída
  descartaria silenciosamente o que foi resolvido antes do bloco, por isso ele lança
  `ConnectError: override(Database) needs a container without resolved clients, found: Database, UserService`.
  Chame `DI.flush()` antes ou use a fixture `global_di`, descrita abaixo.
- **Um substituto por classe.** Chamar `mock(cls)` de novo retorna o substituto já
  registrado; `mock(cls, other)` e `override(cls)` lançam `ConnectError: Database already has a
  replacement`.
- **Substitutos não são conectados.** Os seus `connect()` / `disconnect()` nunca são chamados, e eles
  não fazem parte das [camadas](#layers).
- **Quanto tempo um substituto dura.** O de `mock()` é descartado pelo próximo `flush()`, inclusive o
  do final de `disconnect()`: um teste que conecta o container mais de uma vez deve usar
  `override()`, cujo substituto sobrevive a todos os `flush()` até o fim do bloco. Uma exceção dentro
  do bloco é propagada sem alterações; sair do bloco normalmente com o container ainda conectado
  lança `ConnectError`.
- **Aninhamento.** Blocos para classes diferentes podem ser aninhados, desde que cada um seja aberto antes de qualquer
  resolução, por exemplo `with DI.override(Database), DI.override(Clock):`; sair do bloco interno mantém o
  substituto do bloco externo.

**Fixtures do pytest.** Instalar o `nuke-di` registra um plugin do pytest com duas fixtures. Nenhuma delas é
autouse, então os testes existentes rodam exatamente como antes:

| Fixture     | Fornece                                                |
|-------------|--------------------------------------------------------|
| `di`        | Um `Dependencies` novo para um teste                   |
| `global_di` | O `DI` global, limpo com `flush()` antes e depois do teste |

Um teste que deixa o container conectado recebe um erro no teardown, e o container é limpo
mesmo assim, então o próximo teste começa do zero:

```python
# test_users.py, with Database, UserService and handler from the Quick start
from nuke_di import Dependencies


async def test_greet(di: Dependencies) -> None:
    di.mock(Database).fetch_user.return_value = "alice"
    users = di.resolve(UserService)
    async with di:
        assert await users.greet(1) == "Hello, alice!"


async def test_handler(global_di: Dependencies) -> None:  # e.g. code that calls DI.inject()
    global_di.mock(Database).fetch_user.return_value = "bob"
    injected = global_di.inject(handler)
    async with global_di:
        assert await injected(2) == "Hello, bob!"


async def test_forgets_to_disconnect(di: Dependencies) -> None:
    di.resolve(UserService)
    await di.connect()
```

```console
$ pytest -q test_users.py
...E                                                                     [100%]
==================================== ERRORS ====================================
_______________ ERROR at teardown of test_forgets_to_disconnect ________________
the test left the container of the "di" fixture connected; its clients were not disconnected, use `async with` or call disconnect()
----------------------------- Captured stdout call -----------------------------
database: connected
=========================== short test summary info ============================
ERROR test_users.py::test_forgets_to_disconnect - Failed: the test left the c...
3 passed, 1 error in 0.01s
```

As fixtures não conseguem desconectar sozinhas um container esquecido: no momento do teardown, o event loop do
teste pode já estar fechado. `global_di` só protege os testes que a solicitam: um teste que usa o `DI`
global sem ela ainda pode deixar clientes para trás para o próximo. Um projeto que define a sua própria
fixture `di` continua usando a dele, já que uma fixture do `conftest.py` tem prioridade sobre a de um plugin;
`pytest -p no:nuke_di` desativa o plugin.

**Um job, diretamente.** Importar o módulo não executa o job, então chame a função com
mocks e parâmetros:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    pg.upsert.assert_awaited_once_with("users", ["row"])
```

**Um job via container**, com os clientes ligados como em produção:

```python
async def test_sync_with_container() -> None:
    deps = Dependencies()
    pg = deps.mock(Postgres)  # mocks first: resolve() and inject() reuse them
    warehouse = deps.mock(Warehouse)
    warehouse.changes.return_value = ["row"]
    injected = deps.inject(sync)

    async with deps:
        await injected(day=datetime.date(2026, 10, 1), tables=["users"])

    assert pg.upsert.await_args_list == [call("users", ["row"])]
```

**Um worker.** `Shutdown.set()` faz o mesmo que o SIGTERM faria:

```python
async def test_consumer_stops_on_shutdown() -> None:
    queue, shutdown = AsyncMock(), Shutdown()

    async def last_message() -> str:
        shutdown.set()  # what SIGTERM would do
        return "message-1"

    queue.get.side_effect = last_message

    await consumer(queue, shutdown)

    queue.get.assert_awaited_once()
```

## <a id="configuration"></a>Configuração

| Variável de ambiente         | Padrão  | Descrição                                          |
|------------------------------|---------|----------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`    | Timeout do `connect()` de um único cliente, em segundos |
| `CONNECT_CONCURRENCY`        | `0`     | Quantos clientes podem se conectar ou desconectar ao mesmo tempo em todo o container; `0` significa sem limite |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`    | Timeout do `disconnect()` de um único cliente, em segundos |
| `SHUTDOWN_GRACE_SECONDS`     | `10`    | Por quanto tempo um worker ou job pode continuar rodando depois do SIGTERM / SIGINT antes de ser cancelado, em segundos; lido na inicialização do processo |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

As configurações do container são lidas quando uma instância de `Dependencies` é criada. Também é possível
passá-las explicitamente:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```

## <a id="errors"></a>Erros

| Exceção                     | Lançada quando                                            |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | O `__init__` de um cliente lançou uma exceção             |
| `ConnectError`              | O `connect()` de um cliente lançou uma exceção, ou o estado do container é inválido (por exemplo, resolver depois de conectar, mockar um cliente já resolvido, usar `override()` em um container com clientes resolvidos) |
| `ConnectTimeoutError`       | O `connect()` de um cliente excedeu `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError`     | O `__init__` de um cliente tem um argumento obrigatório que não é cliente, `inject()` recebeu uma função com um argumento sem type hint, ou um parâmetro de ponto de entrada tem um tipo não suportado ou uma flag conflitante; veja [Quando a árvore não pode ser construída](#when-the-tree-cannot-be-built) |
| `CircularDependencyError`   | Clientes dependem uns dos outros em ciclo; subclasse de `InvalidSignatureError` |
| `UsageError`                | A linha de comando de um worker ou job não corresponde aos seus parâmetros; registrado em `Run.error`, código de saída `2` |

`InitializeDependencyError` e `ConnectError` herdam de `SystemExit`: espera-se que uma aplicação
cujas dependências não conseguem subir seja encerrada. Capture-as explicitamente se precisar de
outro comportamento; a exceção original fica disponível em `__cause__`.

O `nuke-di` faz log pelo módulo padrão `logging`, no logger `nuke_di`, com
[campos estruturados](#startup-metrics-and-structured-logs) para pipelines de logs.

## <a id="development"></a>Desenvolvimento

```bash
make install   # uv sync --locked
make check     # ruff, mypy and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

A cobertura de linhas e de branches é de 100%, e o CI falha se ela cair abaixo disso
(`fail_under = 100` no `pyproject.toml`).

### <a id="releases"></a>Releases

Todo merge em `master` é uma release. O workflow `Release` publica no PyPI a versão definida no
`pyproject.toml`, cria a tag `vX.Y.Z` e gera uma release no GitHub a partir da seção correspondente do
`CHANGELOG.md`. Por isso, cada pull request traz a sua própria versão: aumente-a com `uv version --bump
patch|minor|major` e transforme `## [Unreleased]` em `## [X.Y.Z] - YYYY-MM-DD`, com um link de comparação
no final do arquivo. O CI verifica isso em todo pull request, e `make check-version` faz a mesma verificação localmente:

```console
$ make check-version
git fetch --quiet --tags origin master
uv run --no-project python scripts/version.py check origin/master
error: version 1.5.0 is not above 1.5.0 on master: bump it, e.g. `uv version --bump minor`
error: v1.5.0 is released already
make: *** [check-version] Error 1

$ uv version --bump patch
...
nuke-di 1.5.0 => 1.5.1
$ make check-version
git fetch --quiet --tags origin master
uv run --no-project python scripts/version.py check origin/master
1.5.1
```

Uma mudança que chega à `master` sem uma versão nova, por exemplo com um push direto, faz o workflow `Release`
falhar antes que qualquer coisa seja construída ou publicada.

## <a id="license"></a>Licença

[MIT](../../LICENSE)
