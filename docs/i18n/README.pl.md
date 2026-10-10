# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](pl/development.md)
[![License](https://img.shields.io/pypi/l/nuke-di)](../../LICENSE)

[English](https://github.com/troyan-dy/nuke-di/blob/master/README.md) · [Русский](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ru.md) · [简体中文](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.zh-CN.md) · [Español](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.es.md) · [Português (Brasil)](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pt-BR.md) · [日本語](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ja.md) · **Polski**

Najprostsze wstrzykiwanie zależności dla asynchronicznych projektów w Pythonie.

Zależności deklaruje się zwykłymi adnotacjami typów. `nuke-di` buduje drzewo zależności,
tworzy każdego klienta tylko raz i zarządza jego asynchronicznym cyklem życia: `connect()` przy starcie
i `disconnect()` przy zamykaniu. Każdy klient startuje, gdy tylko połączą się jego własne zależności,
współbieżnie ze wszystkimi innymi klientami, którzy są gotowi.

Do tego jeden dekorator zamienia funkcję asynchroniczną w proces z parametrami wiersza poleceń,
a handlery FastAPI, Litestar i FastStream przyjmują klientów po adnotacji typu w ten sam sposób.

Biblioteka została wydzielona z warstwy DI produkcyjnego frameworka do mikroserwisów w Pythonie
i nie ma żadnych zależności w czasie działania.

- [Instalacja](#installation) · [Szybki start](#quick-start) · [Zasady](#principles) · [Wydajność](#performance)
- Przykłady: [job z argumentami wiersza poleceń](#a-job-with-command-line-arguments) · [FastAPI](#fastapi)
- [Dokumentacja](#documentation)

## <a id="installation"></a>Instalacja

```bash
pip install nuke-di
```

Wymaga Pythona 3.11+.

## <a id="quick-start"></a>Szybki start

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

Co się stało:

1. `DI.inject(handler)` odczytał adnotacje typów funkcji `handler`, znalazł klienta `UserService`,
   zauważył, że w swoim `__init__` potrzebuje on `Database`, i zbudował oba. `user_id: int` nie jest
   klientem, więc pozostaje zwykłym argumentem.
2. `async with DI` wywołał `connect()` na każdym zbudowanym kliencie, zaczynając od zależności.
3. `injected(42)` wywołał `handler(42, users=<UserService>)`.
4. Wyjście z bloku `async with` wywołało `disconnect()` w odwrotnej kolejności.

## <a id="principles"></a>Zasady

- **Zależność to klasa.** Podklasa `Client` z `__init__` z adnotacjami typów i asynchronicznymi
  `connect()` / `disconnect()` to cały model: żadnych providerów, modułów, rejestracji ani zasięgów
  (scopes) do konfigurowania. Obiekt z zewnętrznej biblioteki staje się zależnością, gdy opakuje się go w taką klasę.
- **Adnotacje typów to okablowanie.** Klient prosi o swoje zależności w `__init__`, a funkcja — w swojej
  sygnaturze. Nic innego ich nie wymienia, więc zmiana nazwy lub dodanie zależności to zwykły refaktoring.
- **Współbieżny start, uporządkowane zamykanie.** Klient łączy się, gdy tylko połączą się jego własne
  zależności, współbieżnie ze wszystkimi innymi klientami, którzy są gotowi, więc powolny klient wstrzymuje
  tylko tych klientów, którzy go potrzebują. Rozłączają się w odwrotnej kolejności,
  a `disconnect()`, który zakończy się błędem, nie zatrzymuje pozostałych.
- **Szybka porażka (fail fast).** Drzewo, którego nie da się zbudować, zgłasza błąd, zanim cokolwiek się połączy,
  z nazwą argumentu i ścieżką do niego, a `mypy` z [wtyczką](pl/clients.md#checking-the-tree-with-mypy)
  zgłasza ten sam błąd, zanim proces wystartuje. Klient, który nie może się połączyć, zatrzymuje aplikację,
  gdy tylko połączeni już klienci zostaną rozłączeni. Nie ma ponownych prób: restart to zadanie orkiestratora.
- **Testy podmieniają, a nie przepinają.** `mock()` i `override()` wstawiają atrapę w miejsce klienta
  na czas jednego testu; testowany kod się nie zmienia.
- **Brak zależności w czasie działania.** Rdzeń korzysta wyłącznie z biblioteki standardowej; integracje
  z frameworkami to dodatki (extras).

Tak przebiega start, na przykładzie z [przewodnika po klientach](pl/clients.md#connect-order): `Consumer`
potrzebuje tylko `Kafka`, więc nie czeka na wolny `Postgres`, a start trwa tyle, ile najdłuższy łańcuch
zależności.

![Sześciu klientów łączących się według własnych zależności: Consumer i Http startują, gdy tylko połączą się Kafka i Redis, start trwa 0.35s](https://raw.githubusercontent.com/troyan-dy/nuke-di/66f74f76407da320cfc97ef22b761d85e298eddd/docs/connect-now.svg)

## <a id="performance"></a>Wydajność

Przy prawdziwych połączeniach kosztem startu jest czekanie, a decyduje o nim struktura drzewa. Backend strony
produktu: API, cztery funkcje i w każdej cztery połączenia po 100–300 ms, razem 21 klientów:

![Strona produktu z 21 klientów: połączenia, funkcje i API. nuke-di i dependency-injector uruchamiają je w 0.34 s, dishka i wireup w 3.41 s](https://raw.githubusercontent.com/troyan-dy/nuke-di/c402c5086426dc28c0886f62656fcf9d901c5de8/docs/product-page.svg)

`nuke-di` łączy każdego klienta, gdy tylko połączą się jego własne zależności, więc start trwa tyle, co
najdłuższy łańcuch, 0,34 s. dishka i wireup łączą klientów jednego po drugim w ramach `get()`: 3,41 s, dziesięć
razy dłużej, a im szersze drzewo, tym większa różnica. Ręczne zebranie czterech funkcji skraca start wireup do
0,95 s, a dishka również, gdy wyłączy się jej lock, ale wtedy klient wspólny dla dwóch funkcji powstaje
dwa razy. dependency-injector startuje równie szybko, gdy każdy klient jest ręcznie napisanym
`Resource`, a zatrzymuje się warstwami i każda warstwa czeka na swojego najwolniejszego klienta: 0,66 s wobec
0,37 s, bo `Checkout` i `EventsProducer`, którym zatrzymanie zajmuje po 300 ms, są w różnych warstwach. injector
nie ma asynchronicznego cyklu życia.

```console
$ uv run python benchmarks/compare.py --only connect --summary
nuke-di 1.14.3 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 17c8815 · N = 10, 100, 1000 · 20 repeats
nuke-di 1.14.3 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

| Lower is better                          | nuke-di     | dishka         | wireup         | dependency-injector | injector |
|------------------------------------------|------------:|---------------:|---------------:|--------------------:|---------:|
| Startup: 8 clients, connect() of 1–60 ms | **70.8 ms** | 156 ms (2.2×)  | 157 ms (2.2×)  | **71.5 ms**         | —        |
| Shutdown: the same 8 clients             | **18.8 ms** | 30.9 ms (1.6×) | 30.5 ms (1.6×) | 26.0 ms (1.4×)      | —        |
| Startup: the product page, 21 clients    | **335 ms**  | 3.41 s (10.2×) | 3.41 s (10.2×) | **337 ms**          | —        |
| Shutdown: the product page               | **365 ms**  | 850 ms (2.3×)  | 850 ms (2.3×)  | 657 ms (1.8×)       | —        |
```

Żadna z trzech bibliotek niczego nie łączy przy tworzeniu kontenera: jeśli aplikacja nie pobierze korzenia
przy starcie, pierwsze żądanie czeka na połączenia i kończy się błędem razem z nimi. `nuke-di` łączy każdego
klienta w `async with DI`, a klient, który nie może się połączyć, zatrzymuje start.

`benchmarks/compare.py` przepuszcza te same drzewa klientów przez dishka, wireup, dependency-injector i
injector, rejestrując te same klasy tak, jak robi to każda biblioteka: zimny kontener z rozwiązanym korzeniem na
klasach nowych dla procesu, ponowne pobranie korzenia i jedno żądanie FastAPI przez integrację każdej biblioteki:

```console
$ uv run python benchmarks/compare.py --size 100 --summary
nuke-di 1.11.1 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 6c2ae10 · N = 100 · 20 repeats
nuke-di 1.11.1 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

| Lower is better                                          | nuke-di        | dishka          | wireup          | dependency-injector | injector        |
|----------------------------------------------------------|---------------:|----------------:|----------------:|--------------------:|----------------:|
| Cold start: a container and a tree of 100 clients        | **541 µs**     | 12.9 ms (23.8×) | 20.0 ms (37.0×) | 1.05 ms (1.9×)      | 1.34 ms (2.5×)  |
| Cold start: the same 100 clients with string annotations | 1.27 ms (1.2×) | 13.7 ms (12.6×) | 21.4 ms (19.6×) | **1.09 ms**         | 1.47 ms (1.3×)  |
| A cached root                                            | 94.1 ns (2.5×) | 261 ns (7.1×)   | 92.6 ns (2.5×)  | **37.0 ns**         | 1.18 µs (31.9×) |
| A FastAPI request with a client                          | **103 µs**     | 107 µs (1.0×)   | 206 µs (2.0×)   | 221 µs (2.2×)       | —               |
```

![nuke-di against other DI libraries: lower is better](../benchmarks/compare.png)

Czy więc `nuke-di` jest najszybszy? Przy budowaniu drzewa z prawdziwymi adnotacjami typów i w żądaniu
FastAPI tak: dependency-injector i injector budują drzewo 2–2,5 razy dłużej, dishka i wireup 24–37 razy
dłużej przez walidację grafu przy tworzeniu kontenera, a wireup i dependency-injector płacą dwa razy więcej
na każde żądanie. Z adnotacjami w postaci napisów dependency-injector, który nie czyta żadnych adnotacji,
jest o jedną piątą szybszy od `nuke-di`. Na korzeniu z cache `nuke-di` idzie łeb w łeb z wireup, a `get()`
dependency-injector napisany w Cythonie wygrywa o około 50 ns, czego żadna aplikacja nie zauważy.

Sam `resolve()` kosztuje 3,5–6,3 µs na klienta, więc drzewo z 1000 klientów powstaje w mniej niż 5,5 ms,
a `connect()` dokłada 13–18 µs na klienta. [docs/benchmarks.md](../benchmarks.md) objaśnia każdy
scenariusz, zapisuje punkt odniesienia dla Pythona 3.11–3.14 i zawiera pełne porównanie wraz z metodą.

## <a id="a-job-with-command-line-arguments"></a>Job z argumentami wiersza poleceń

Jeden dekorator zamienia funkcję asynchroniczną w główny program procesu. Klienci są wstrzykiwani, a każdy
inny argument z adnotacją staje się opcją wiersza poleceń, z typem i walidacją:

```python
# sync.py
import datetime
import enum
from typing import Annotated

from nuke_di import Client, Option, job


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")

    async def upsert(self, table: str, rows: list[str]) -> None:
        print(f"postgres: upserted {len(rows)} rows into {table}")


class Warehouse(Client):
    async def changes(self, table: str, day: datetime.date) -> list[str]:
        return [f"{table}:{day}:{n}" for n in range(3)]


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

Bez `main()`, bez `asyncio.run()`, bez `argparse`: dekorator rozwiązuje i łączy klientów, parsuje
wiersz poleceń, wykonuje funkcję i kończy się kodem wyjścia, który coś znaczy:

```console
$ python sync.py --day 2026-10-01
postgres: connected
sync: INCREMENTAL copy of 2026-10-01
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected

$ python sync.py -d 2026-10-01 -t users --mode FULL --dry-run
postgres: connected
sync: FULL copy of 2026-10-01
sync: would upsert 3 rows into users
postgres: disconnected
```

`--help` jest generowane z sygnatury i docstringu (Python 3.13+ wypisuje `-d, --day DAY` zamiast
`-d DAY, --day DAY`):

```console
$ python sync.py --help
usage: sync.py [-h] -d DAY [-t TABLES] [--mode {INCREMENTAL,FULL}]
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

Błędny wiersz poleceń zostaje odrzucony, zanim jakikolwiek klient zostanie połączony, z kodem wyjścia `2`:

```console
$ python sync.py -d 2026-10-01 --mode full
usage: sync.py [-h] -d DAY [-t TABLES] [--mode {INCREMENTAL,FULL}]
               [--dry-run | --no-dry-run]
sync.py: error: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
Run sync.sync failed: argument --mode: invalid choice: 'full' (choose from INCREMENTAL, FULL)
$ echo $?
2
```

`@worker` robi to samo dla procesu, który działa aż do SIGTERM, z łagodnym zamykaniem. Oba opisuje
rozdział [Workery i joby](pl/workers-and-jobs.md).

## <a id="fastapi"></a>FastAPI

Operacja ścieżki (path operation) przyjmuje klienta po adnotacji typu, bez `Depends` i bez `inject()` w każdym handlerze.
Plik `app/clients.py` zawiera klasy `Database` i `UserService` ze [Szybkiego startu](#quick-start), bez jego
`main()`:

```bash
pip install "nuke-di[fastapi]"
```

```python
# app/api.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di.fastapi import setup

app = FastAPI()
setup(app)  # before the routes: clients connect on startup, disconnect on shutdown


@app.get("/users/{user_id}")
async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


@app.get("/me")
async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user
```

```console
$ uvicorn app.api:app
INFO:     Started server process [55625]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51602 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51604 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [55625]
```

```console
$ curl localhost:8000/users/42
"Hello, user-42!"
$ curl localhost:8000/me -H "X-User-Id: 7"
"user-7"
```

Klienci łączą się przy starcie i rozłączają przy zamykaniu, a zależność taka jak `current_user` przyjmuje
klientów w ten sam sposób. Import aplikacji niczego nie buduje, więc test podmienia klienta, zanim `TestClient`
ją wystartuje:

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
1 passed in 0.23s
```

Routery, websockety i własny lifespan aplikacji opisuje rozdział [FastAPI](pl/fastapi.md);
[Litestar](pl/litestar.md) i [FastStream](pl/faststream.md) działają w ten sam sposób.

## <a id="documentation"></a>Dokumentacja

- [Klienci](pl/clients.md): `Client` i `NotSingletonClient`, cykl życia, klienci jako dataclass,
  kolejność łączenia, czasy startu, graf zależności, błędy połączenia i rozwiązywania
- [Kontener](pl/container.md): `Dependencies` i globalny `DI`, `resolve()`, `inject()`,
  `mock()`, `override()`
- [Workery i joby](pl/workers-and-jobs.md): `@job` i `@worker`, parametry wiersza poleceń,
  `Shutdown`, okres karencji, zadania w tle, kody wyjścia, hooki, Kubernetes
- Frameworki: [FastAPI](pl/fastapi.md), [Litestar](pl/litestar.md),
  [FastStream](pl/faststream.md),
  [serwery wewnątrz workera](pl/servers-in-workers.md) dla grpc.aio, aiohttp, websockets, APScheduler,
  Textual i Temporal oraz
  [pisanie integracji](pl/integrations.md) z innym frameworkiem przez `nuke_di.integration`
- [Testowanie](pl/testing.md): `mock()`, `override()`, fixture'y pytest, sprawdzanie okablowania
- [Konfiguracja](pl/configuration.md): timeouty, współbieżność i okres karencji
- [Błędy](pl/errors.md): każdy wyjątek i sytuacja, w której jest zgłaszany
- [Przykłady](../../examples/README.md): 21 scenariuszy gotowych do uruchomienia, od jednorazowego skryptu i workera kolejki
  po FastAPI, Litestar, FastStream, Starlette i cały serwis, każdy z wynikiem i testami
- [Benchmarki](../benchmarks.md): każdy scenariusz, punkt odniesienia dla Pythona 3.11–3.14 i porównanie
  z innymi bibliotekami
- [Agenty programistyczne](pl/agents.md): Agent Skill, blok dla `AGENTS.md`,
  [`llms.txt`](https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms.txt), Context7, graf jako JSON
- [Rozwój](pl/development.md): sprawdzenia, pokrycie i wydania

## <a id="license"></a>Licencja

[MIT](../../LICENSE)
