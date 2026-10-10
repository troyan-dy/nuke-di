# <a id="workers-and-jobs"></a>Workery i joby

[English](../../guide/workers-and-jobs.md) · [Русский](../ru/workers-and-jobs.md) · [简体中文](../zh-CN/workers-and-jobs.md) · [Español](../es/workers-and-jobs.md) · [Português (Brasil)](../pt-BR/workers-and-jobs.md) · [日本語](../ja/workers-and-jobs.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

Jeden dekorator wystarczy, by funkcja asynchroniczna stała się głównym programem procesu:

| Dekorator | Działa                                                   |
|-----------|----------------------------------------------------------|
| `@job`    | Raz: proces kończy się, gdy funkcja zwróci wynik         |
| `@worker` | Dopóki proces nie otrzyma SIGTERM lub SIGINT             |

Przykłady w tej sekcji korzystają ze wspólnego modułu klientów:

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

## <a id="your-first-job"></a>Twój pierwszy job

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

To cały program: bez `main()`, bez `asyncio.run()`, bez `if __name__ == "__main__"`.
Proces rozwiązuje klientów z globalnego kontenera `DI`, łączy ich, wykonuje funkcję,
rozłącza klientów i kończy się [kodem wyjścia](#exit-codes). Harmonogram nie jest częścią
biblioteki: o tym, kiedy job się uruchomi, decyduje CronJob w Kubernetes, timer systemd albo crontab.

`nuke-di` loguje każde uruchomienie w loggerze `nuke_di`. Aby to zobaczyć, skonfiguruj logowanie
nad dekoratorem:

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
INFO  nuke_di.core: Connected 4 clients in 0.00s (slowest: Postgres 0.00s, Shutdown 0.00s, Warehouse 0.00s)
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
INFO  nuke_di.run: Run app.jobs.sync.sync finished with exit code 0 in 0.002s
```

### <a id="one-entrypoint-per-module-defined-last"></a>Jeden punkt wejścia na moduł, zdefiniowany na końcu

Gdy moduł jest uruchamiany jako `__main__`, dekorator od razu wykonuje funkcję, a proces
kończy się w tym miejscu:

```python
# app/jobs/sync.py
DI.mock(Warehouse, FakeWarehouse())  # runs: code above the decorator is fine


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None: ...


print("never printed")  # never runs under `python -m app.jobs.sync`
```

**Trzymaj jeden punkt wejścia na moduł i definiuj go na końcu.** Przy zwykłym imporcie, np. z testu,
dekorator zwraca funkcję bez zmian i nic się nie uruchamia. Dekorowana funkcja musi być
zadeklarowana przez `async def`, w przeciwnym razie już przy imporcie zgłaszany jest `TypeError`.

## <a id="parameters"></a>Parametry

Każdy argument z adnotacją, który nie jest klientem, staje się opcją wiersza poleceń. Oto ten sam
job, który teraz potrafi skopiować dowolny dzień, wybrane tabele, a także działać na sucho (dry run):

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

`pg` i `warehouse` są klientami i zostają wstrzyknięte; `day`, `tables`, `mode` i `dry_run` pochodzą
z wiersza poleceń:

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

`--help` jest generowane z sygnatury i docstringu. Niczego nie łączy
(Python 3.13+ wypisuje `-d, --day DAY` zamiast `-d DAY, --day DAY`):

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

Błędny wiersz poleceń zostaje odrzucony **zanim jakikolwiek klient zostanie rozwiązany lub połączony**,
z kodem wyjścia `2`:

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

Dwie pierwsze linie każdego błędu wypisuje `argparse`; linia `Run ... failed` to rekord
`ERROR` loggera `nuke_di`, więc podlega twojej konfiguracji logowania.
Skróty nie są akceptowane: `--dry` nie zostanie uznane za `--dry-run`.

### <a id="supported-types"></a>Obsługiwane typy

| Adnotacja                                     | Wiersz poleceń                  | Przykład                        |
|-----------------------------------------------|---------------------------------|---------------------------------|
| `str`, `int`, `float`, `pathlib.Path`         | `--name VALUE`                  | `--limit 10`                    |
| `bool`                                        | `--name` / `--no-name`          | `--dry-run`                     |
| `datetime.date`, `datetime.datetime`          | ISO 8601                        | `--since 2026-10-01T12:00:00`   |
| dowolny `Enum`                                | **nazwa** elementu, dokładnie tak, jak jest zapisana | `--mode FULL` |
| `list[T]` z dowolnym z powyższych poza `bool` | powtórzona opcja                | `--table users --table orders`  |
| `T \| None`                                   | jak `T`                         | `--limit 10`                    |

Zasady:

- **Nazwa.** Opcja nazywa się tak jak argument, z `_` zamienionym na `-`:
  `dry_run` to `--dry-run`. Nie ma argumentów pozycyjnych, więc dodanie parametru nigdy
  nie psuje istniejącego wywołania.
- **Wymagana czy nie.** Argument bez wartości domyślnej to opcja wymagana. Argument z wartością
  domyślną jest opcjonalny, a gdy opcję pominięto, używana jest wartość domyślna samej funkcji.
- **`Option`.** `Annotated[T, Option(help=..., short=...)]` dodaje tekst pomocy i jednoliterowy
  alias, np. `-d`. Oba są opcjonalne.
- **Bez parametrów.** Punkt wejścia bez parametrów i tak parsuje swój wiersz poleceń:
  odpowiada na `--help` i odrzuca każdy argument z kodem wyjścia `2`.

Poniższe sygnatury to błędy w kodzie, a nie w wierszu poleceń. Kończą uruchomienie błędem
`InvalidSignatureError` i kodem wyjścia `1`:

```python
async def sync(day: dict[str, int]) -> None: ...  # unsupported type
async def sync(pg: Annotated[Postgres, Option(help="...")]) -> None: ...  # Option on a client
async def sync(help: bool = False) -> None: ...  # clashes with --help
async def sync(day: int, /) -> None: ...  # positional-only
```

### <a id="parameters-in-tests"></a>Parametry w testach

Dekorowana funkcja nadal jest zwykłą korutyną, więc test przekazuje parametry jako argumenty
nazwane:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    warehouse.changes.assert_awaited_once_with("users", datetime.date(2026, 10, 1))
    pg.upsert.assert_awaited_once_with("users", ["row"])
```

## <a id="your-first-worker"></a>Twój pierwszy worker

Worker działa, dopóki proces nie zostanie poproszony o zatrzymanie. Zależy od klienta `Shutdown`,
który zostaje ustawiony przy pierwszym SIGTERM lub SIGINT, i dokańcza bieżącą porcję pracy:

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

Ctrl+C w trakcie przetwarzania trzeciej wiadomości: wiadomość zostaje dokończona, pętla się kończy,
klienci się rozłączają.

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

`Shutdown` ma trzy metody:

| Metoda           | Opis                                                                      |
|------------------|---------------------------------------------------------------------------|
| `is_set()`       | Czy zamykanie już się rozpoczęło; sprawdzaj to między porcjami pracy      |
| `await wait()`   | Blokuje, dopóki nie rozpocznie się zamykanie                              |
| `set()`          | Ręcznie rozpoczyna zamykanie, np. w teście                                |

Poza workerem i jobem nikt go nie ustawia, więc pętla zależna od `Shutdown` działa bez zmian
także w aplikacji webowej. Worker, który sam zwróci wynik albo zgłosi wyjątek, również kończy
proces: ponowne uruchomienie to zadanie orkiestratora.

Serwer bez własnego wstrzykiwania zależności, taki jak serwer gRPC, aplikacja aiohttp czy worker
Temporal, działa wewnątrz workera tak samo: zobacz [Serwery wewnątrz workera](servers-in-workers.md).

Na Windowsie obsługiwany jest tylko SIGINT (Ctrl+C); SIGTERM zachowuje się domyślnie.

## <a id="grace-period"></a>Okres karencji

Worker, który ignoruje `Shutdown`, zostaje anulowany po `SHUTDOWN_GRACE_SECONDS` (domyślnie `10`):

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

Drugi sygnał anuluje punkt wejścia natychmiast, bez czekania na koniec okresu karencji, np.
dwukrotne Ctrl+C:

```console
$ python -m app.workers.stubborn
queue: connected
stubborn: processing message-1
^C^C
Second SIGINT, cancelling run app.workers.stubborn.stubborn
queue: disconnected
```

Sygnał, który nadejdzie, gdy klienci jeszcze się łączą, przerywa start, a klienci, którzy
zdążyli się połączyć, zostają rozłączeni.

W najgorszym przypadku zatrzymanie procesu trwa
`SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × the longest chain of dependencies`. Przy
wartościach domyślnych łańcuch dwóch klientów zużywa całe domyślne `terminationGracePeriodSeconds` Kubernetesa, czyli 30 sekund,
więc przy głębszych drzewach zmniejsz timeouty albo wydłuż okres karencji.

## <a id="background-tasks"></a>Zadania w tle

`BackgroundTasks` to klient, który nadzoruje korutyny działające równolegle z punktem wejścia.
W przeciwieństwie do gołego `asyncio.create_task()` błąd zadania nigdy nie przepada: trafia do logu
razem z tracebackiem i kończy błędem cały proces.

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

Worker został anulowany bez okresu karencji: pętla w tle, która padła, nie może zostawić
przy życiu procesu, który nic nie robi. Gdy proces zatrzymuje się z dowolnego powodu, zadania są
anulowane, a ich zakończenie jest oczekiwane **zanim** którykolwiek klient się rozłączy, więc nigdy
nie działają na zamkniętych klientach.

| Metoda                     | Opis                                                            |
|----------------------------|-----------------------------------------------------------------|
| `spawn(coro, name=None)`   | Uruchamia `coro` jako zadanie i trzyma do niego referencję, dopóki się nie zakończy |
| `watch(callback)`          | Wywołuje `callback(exc)` dla każdego zadania, które zakończy się błędem |
| `await stop()`             | Anuluje wszystkie zadania i czeka na każde z nich; wywołuje je `disconnect()` |

Poza workerem i jobem, np. w zwykłym `async with DI`, błędy są tylko logowane, a zadania
zostają anulowane przy `disconnect()`.

## <a id="exit-codes"></a>Kody wyjścia

Wygrywa pierwsza pasująca reguła:

| Warunek                                                                                             | Kod wyjścia    |
|-----------------------------------------------------------------------------------------------------|----------------|
| Nieprawidłowy wiersz poleceń (`UsageError`)                                                         | `2`            |
| Wyjątek: w sygnaturze, przy rozwiązywaniu lub łączeniu klientów, w punkcie wejścia, w zadaniu w tle | `1`            |
| Otrzymano sygnał zakończenia                                                                        | `128 + signum` |
| W pozostałych przypadkach                                                                           | `0`            |

SIGTERM daje `143`, a SIGINT — `130`. Job, który zauważy zamykanie i poprawnie zwróci wynik,
i tak kończy się kodem `128 + signum`: jego praca została przerwana i scheduler nie może uznać jej
za ukończoną.

Kody są przeznaczone dla tego, co uruchamia proces:

```bash
python -m app.jobs.sync --day 2026-10-01
case $? in
  0)       echo "synced" ;;
  2)       echo "fix the command line, retrying will not help" ;;
  130|143) echo "interrupted, safe to run again" ;;
  *)       echo "failed, see the log" ;;
esac
```

## <a id="hooks"></a>Hooki

Hooki obserwują każde uruchomienie, np. po to, by wysyłać metryki albo otworzyć span tracingu:

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

`on_start` jest wywoływane w kolejności z listy, zanim klienci zostaną rozwiązani; `on_finish` — w odwrotnej
kolejności, po ich rozłączeniu, więc widzi końcowy stan `Run`, łącznie z błędami
połączenia:

| Pole `Run`    | Wartość                                                                |
|---------------|------------------------------------------------------------------------|
| `name`        | Moduł i funkcja, np. `app.jobs.report.report`                          |
| `kind`        | `"job"` lub `"worker"`                                                 |
| `started_at`  | `datetime` w UTC                                                       |
| `finished_at` | `datetime` w UTC, ustawiane przed `on_finish`                          |
| `exit_code`   | Kod wyjścia procesu, ustawiany przed `on_finish`                       |
| `error`       | Wyjątek, który zakończył uruchomienie błędem, np. `UsageError`, albo `None` |
| `signal`      | Pierwszy otrzymany sygnał zakończenia albo `None`                      |
| `clients`     | Po jednym `ClientTiming` na klienta: czasy i wyniki łączenia i rozłączania; pusta, jeśli uruchomienie padło przed połączeniem |

Hooki to zwykłe obiekty, a nie klienci: same zarządzają swoimi zasobami. Wyjątek w hooku
trafia do logu i nie zmienia kodu wyjścia. `--help` nie jest uruchomieniem, więc hooki go nie widzą.

### <a id="startup-metrics-and-structured-logs"></a>Metryki startu i logi strukturalne

`run.clients` to miejsce na eksport metryk startu: `on_finish` widzi, ile każdy klient łączył
się i rozłączał. Ponadto każdy rekord logu `nuke_di` niesie pola strukturalne, więc formatter
JSON może filtrować i agregować po kliencie bez parsowania wiadomości:

```python
# app/jobs/startup.py
import json
import logging

from nuke_di import Run, job

from app.clients import Postgres, Warehouse


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {key: getattr(record, key) for key in ("run", "client", "duration") if hasattr(record, key)}
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
{"level": "INFO", "message": "Connected 4 clients in 0.00s (slowest: Postgres 0.00s, Shutdown 0.00s, Warehouse 0.00s)", "run": "app.jobs.startup.startup", "duration": 0.00022179202642291784}
startup: done
postgres: disconnected
warehouse: disconnected
{"level": "INFO", "message": "Run app.jobs.startup.startup finished with exit code 0 in 0.001s", "run": "app.jobs.startup.startup", "duration": 0.001171}
metric: Shutdown connect=0.000s ok
metric: BackgroundTasks connect=0.000s ok
metric: Postgres connect=0.000s ok
metric: Warehouse connect=0.000s ok
```

| Pole       | Obecne w                                                                      |
|------------|-------------------------------------------------------------------------------|
| `run`      | Każdym rekordzie powstałym wewnątrz workera lub joba, także kontenera: nazwa uruchomienia |
| `client`   | Każdym rekordzie o jednym kliencie: rozwiązywanie, łączenie, rozłączanie, błędy |
| `duration` | Sekundy: połączony lub rozłączony klient, podsumowanie startu, zakończone uruchomienie |

Każde uruchomienie łączy też własnych klientów `Shutdown` i `BackgroundTasks`, więc pojawiają
się oni w `run.clients` i w podsumowaniu.

## <a id="running-in-kubernetes"></a>Uruchamianie w Kubernetes

Job odpowiada CronJobowi, a worker — Deploymentowi. Daj workerowi wystarczająco dużo
`terminationGracePeriodSeconds` na [budżet zamykania](#grace-period):

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
      terminationGracePeriodSeconds: 30  # >= SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × longest chain
      containers:
        - name: consumer
          image: registry.example.com/app:1.0
          command: ["python", "-m", "app.workers.consumer"]
          env:
            - {name: SHUTDOWN_GRACE_SECONDS, value: "15"}
```

Jednorazowy backfill to ten sam obraz z innymi parametrami:

```bash
kubectl run sync-backfill --rm -it --restart=Never --image=registry.example.com/app:1.0 \
  --command -- python -m app.jobs.sync --day 2026-09-30 --mode FULL
```
