# <a id="workers-and-jobs"></a>Workers e jobs

[English](../../guide/workers-and-jobs.md) · [Русский](../ru/workers-and-jobs.md) · [简体中文](../zh-CN/workers-and-jobs.md) · [Español](../es/workers-and-jobs.md) · **Português (Brasil)** · [日本語](../ja/workers-and-jobs.md) · [Polski](../pl/workers-and-jobs.md)

← [Documentação](../README.pt-BR.md#documentation)

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

## <a id="your-first-job"></a>Seu primeiro job

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

### <a id="one-entrypoint-per-module-defined-last"></a>Um ponto de entrada por módulo, definido por último

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

## <a id="parameters"></a>Parâmetros

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

### <a id="supported-types"></a>Tipos suportados

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

### <a id="parameters-in-tests"></a>Parâmetros nos testes

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

## <a id="your-first-worker"></a>Seu primeiro worker

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

## <a id="grace-period"></a>Período de tolerância

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

## <a id="background-tasks"></a>Tarefas em segundo plano

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

## <a id="exit-codes"></a>Códigos de saída

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

## <a id="hooks"></a>Hooks

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

### <a id="startup-metrics-and-structured-logs"></a>Métricas de inicialização e logs estruturados

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

## <a id="running-in-kubernetes"></a>Execução no Kubernetes

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
