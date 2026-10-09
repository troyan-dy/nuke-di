# nuke-di

[![PyPI](https://img.shields.io/pypi/v/nuke-di)](https://pypi.org/project/nuke-di/)
[![Python](https://img.shields.io/pypi/pyversions/nuke-di)](https://pypi.org/project/nuke-di/)
[![CI](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/nuke-di/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](#development)
[![License](https://img.shields.io/pypi/l/nuke-di)](../../LICENSE)

[English](https://github.com/troyan-dy/nuke-di/blob/master/README.md) · [Русский](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ru.md) · [简体中文](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.zh-CN.md) · [Español](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.es.md) · [Português (Brasil)](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.pt-BR.md) · [日本語](https://github.com/troyan-dy/nuke-di/blob/master/docs/i18n/README.ja.md) · **Polski**

Najprostsze wstrzykiwanie zależności dla asynchronicznych projektów w Pythonie.

Zależności deklaruje się zwykłymi adnotacjami typów. `nuke-di` buduje drzewo zależności,
tworzy każdego klienta tylko raz i zarządza jego asynchronicznym cyklem życia: `connect()` przy starcie
i `disconnect()` przy zamykaniu. Niezależni klienci startują współbieżnie, warstwa po warstwie,
od najgłębszych zależności w górę.

Do tego jeden dekorator zamienia funkcję asynchroniczną w proces: **job**, który wykonuje się
raz, albo **worker**, który działa, dopóki się go nie zatrzyma — z parametrami wiersza poleceń, łagodnym
zamykaniem po SIGTERM i kodami wyjścia, które coś znaczą. Handlery FastAPI, Litestar i FastStream
przyjmują klientów po adnotacji typu w ten sam sposób.

Biblioteka została wydzielona z warstwy DI produkcyjnego frameworka do mikroserwisów w Pythonie
i nie ma żadnych zależności w czasie działania.

- [Instalacja](#installation)
- [Szybki start](#quick-start)
- [Klienci](#clients): [singletony](#client-and-notsingletonclient), [cykl życia](#connect-and-disconnect), [klienci jako dataclass](#dataclass-clients), [warstwy](#layers), [czasy startu](#startup-timings), [graf](#the-graph), [błędy połączenia](#when-a-client-fails-to-connect), [błędy rozwiązywania](#when-the-tree-cannot-be-built)
- [Kontener](#the-container)
- [Workery i joby](#workers-and-jobs): [job](#your-first-job), [parametry](#parameters), [worker](#your-first-worker), [okres karencji](#grace-period), [zadania w tle](#background-tasks), [kody wyjścia](#exit-codes), [hooki](#hooks), [Kubernetes](#running-in-kubernetes)
- Frameworki: [FastAPI](#fastapi), [Litestar](#litestar), [FastStream](#faststream)
- [Testowanie](#testing)
- [Konfiguracja](#configuration) · [Błędy](#errors) · [Wydajność](#performance) · [Rozwój](#development)

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

## <a id="clients"></a>Klienci

### <a id="client-and-notsingletonclient"></a>Client i NotSingletonClient

Każda zależność jest podklasą jednej z dwóch klas bazowych:

| Klasa bazowa         | Instancje                                                  |
|----------------------|------------------------------------------------------------|
| `Client`             | Singleton: jedna instancja na kontener                     |
| `NotSingletonClient` | Nowa instancja dla każdego konsumenta, który ją deklaruje  |

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

Klient deklaruje własne zależności jako argumenty `__init__` z adnotacjami typów. Wstrzykiwane są
tylko argumenty, których adnotacja jest typem klienta, a rozwiązywanie działa rekurencyjnie.

### <a id="connect-and-disconnect"></a>connect() i disconnect()

Nadpisz asynchroniczne metody `connect()` / `disconnect()`, aby otwierać i zwalniać zasoby, takie
jak pule połączeń. `__init__` tylko zapamiętuje zależności; wszystko, co wykonuje I/O, należy
do `connect()`:

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

Każde `connect()` jest ograniczone przez `CONNECT_TIMEOUT_SECONDS` (domyślnie `30`), a każde
`disconnect()` przez `DISCONNECT_TIMEOUT_SECONDS` (domyślnie `10`). Błąd lub zawieszenie się
`disconnect()` trafia do logu, a pozostali klienci i tak się zamykają.

### <a id="dataclass-clients"></a>Klienci jako dataclass

`client_dataclass` zamienia klasę jednocześnie w `Client` i w dataclass, więc jej pola
stają się wstrzykiwanymi zależnościami. Dziedzicz też po `Client`: dekorator jest typowany jako
tożsamość, więc to klasa bazowa mówi mypy i pyrightowi, że `Checkout` jest klientem; bez niej klasa
jest klientem tylko w czasie wykonania:

```python
from nuke_di import Client, Dependencies, client_dataclass


class Postgres(Client):
    pass


class Payments(Client):
    pass


@client_dataclass(frozen=True)
class Checkout(Client):
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

Przyjmuje te same argumenty nazwane co `dataclasses.dataclass`.

### <a id="layers"></a>Warstwy

Klienci łączą się współbieżnie, warstwami. Klienci bez zależności tworzą warstwę 0;
każdy inny klient leży o jedną warstwę wyżej niż jego najwyżej położona zależność. Warstwa startuje
dopiero wtedy, gdy poprzednia jest już połączona, więc klient nigdy nie łączy się przed własnymi
zależnościami. `disconnect()` przechodzi przez warstwy w odwrotnej kolejności.

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

Log `DEBUG` loggera `nuke_di` pokazuje warstwy:

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

Kolejność ustalana jest wyłącznie na podstawie zależności zadeklarowanych w `__init__`. Jeśli klient
potrzebuje, żeby inny był połączony wcześniej, zadeklaruj go jako zależność. Ustaw `CONNECT_CONCURRENCY`,
aby ograniczyć liczbę klientów łączących się jednocześnie.

### <a id="startup-timings"></a>Czasy startu

Kontener mierzy `connect()` i `disconnect()` każdego klienta, więc powolny start sam wskazuje
winnego. Po udanym `connect()` loguje podsumowanie na poziomie `INFO` oraz `WARNING` dla
każdego klienta, który zużył ponad połowę `CONNECT_TIMEOUT_SECONDS`, na długo zanim ten
klient zacznie padać z powodu timeoutu:

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

`deps.timings` przechowuje po jednym `ClientTiming` na każdego klienta ostatniego `connect()`,
w kolejności łączenia. Lista przetrwa `disconnect()`, więc można ją odczytać po zatrzymaniu
kontenera. W aplikacji FastAPI lifespan przekazany do `FastAPI()` działa wewnątrz połączonego
kontenera, więc widzi czasy łączenia. Worker lub job dostaje tę samą listę w
[`Run.clients`](#startup-metrics-and-structured-logs).

| Pole `ClientTiming`  | Wartość |
|----------------------|---------|
| `name`               | Nazwa klasy klienta |
| `layer`              | [Warstwa](#layers) klienta |
| `connect`            | Sekundy spędzone w `connect()`, bez czekania na `CONNECT_CONCURRENCY`; `None`, jeśli `connect()` nigdy się nie uruchomił |
| `connect_outcome`    | `"ok"`, `"failed"`, `"timed_out"`, `"cancelled"` albo `None`, jeśli `connect()` się nie zaczął |
| `disconnect`, `disconnect_outcome` | To samo dla `disconnect()`; `None`, dopóki klient się nie rozłączy |

Gdy klient nie może się połączyć, klienci jego warstwy, którzy wciąż się łączą, dostają
`"cancelled"`, wyższe warstwy zostają z `None`, a klienci już połączeni są wycofywani, więc
dostają `disconnect_outcome`. Biblioteka tylko mierzy: eksport czasów jako metryk lub spanów
należy do twojego kodu.

### <a id="the-graph"></a>Graf

Graf zależności istnieje tylko wewnątrz działającego procesu: log `DEBUG` powyżej to jedyne miejsce,
które pokazuje, jakich klientów ściąga punkt wejścia i w której warstwie każdy z nich się łączy.
`graph()` zwraca ten sam obraz jako dane, przed `connect()` albo po nim. Klienci z przykładu o
[warstwach](#layers), bez swojego `connect()`:

```python
# graph.py
from nuke_di import Client, Dependencies


class Postgres(Client):
    pass


class Redis(Client):
    pass


class Payments(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Checkout(Client):
    def __init__(self, pg: Postgres, redis: Redis, payments: Payments) -> None:
        self.pg, self.redis, self.payments = pg, redis, payments


deps = Dependencies()
deps.resolve(Checkout)
nodes = {node.name: node for node in deps.graph().nodes}
for node in nodes.values():
    print(f"{node.name:<8} layer {node.layer}  needs {list(node.dependencies)}")
print("shared:", nodes["Checkout"].dependencies["pg"] is nodes["Payments"].dependencies["pg"])
print(deps.graph().to_mermaid())
```

```console
$ python graph.py
Postgres layer 0  needs []
Redis    layer 0  needs []
Payments layer 1  needs ['pg']
Checkout layer 2  needs ['pg', 'redis', 'payments']
shared: True
graph BT
  subgraph layer0 [layer 0]
    Postgres
    Redis
  end
  subgraph layer1 [layer 1]
    Payments
  end
  subgraph layer2 [layer 2]
    Checkout
  end
  Postgres --> Payments
  Postgres --> Checkout
  Redis --> Checkout
  Payments --> Checkout
```

GitHub renderuje tekst Mermaid w README, pull requeście lub issue, więc projekt może pokazać swoją
architekturę bez działającego procesu:

```mermaid
graph BT
  subgraph layer0 [layer 0]
    Postgres
    Redis
  end
  subgraph layer1 [layer 1]
    Payments
  end
  subgraph layer2 [layer 2]
    Checkout
  end
  Postgres --> Payments
  Postgres --> Checkout
  Redis --> Checkout
  Payments --> Checkout
```

`Graph.nodes` trzyma po jednym `Node` na rozwiązanego klienta, w kolejności rozwiązywania, więc klient
jest po swoich zależnościach. To zrzut: `flush()` go opróżnia, poza Replacement otwartych bloków
`override()`, które przetrwają każdy `flush()`.

| Pole `Node`    | Wartość |
|----------------|---------|
| `name`         | Nazwa klasy klienta |
| `cls`          | Klasa, o którą prosili konsumenci |
| `singleton`    | `True` dla `Client`, `False` dla `NotSingletonClient` |
| `layer`        | [Warstwa](#layers) klienta; `None` dla Replacement, który nigdy się nie łączy |
| `replacement`  | Obiekt zarejestrowany przez `mock()` lub `override()` w miejsce `cls`; `None` dla prawdziwego klienta |
| `dependencies` | Klienci argumentów `__init__`, według nazwy argumentu |

`NotSingletonClient` dostaje po węźle na instancję, wszystkie o tej samej nazwie; `to_mermaid()` numeruje je
od drugiego (`Session`, `Session_2`). Replacement jest rysowany poza warstwami, z przerywaną ramką i nazwą
obiektu na jego miejscu: `Postgres: AsyncMock`. Węzły porównują się przez tożsamość, więc
`shared: True` powyżej mówi, że `Checkout` i `Payments` dostali ten sam `Postgres`.

### <a id="when-a-client-fails-to-connect"></a>Gdy klient nie może się połączyć

Jeśli klientowi nie uda się połączyć, reszta jego warstwy zostaje anulowana, a kolejne warstwy w ogóle
nie startują. Klienci, którzy zdążyli się połączyć, zostają rozłączeni — warstwami, w odwrotnej
kolejności — a kontener zostaje rozłączony i pusty:

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
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
postgres: disconnected
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable <- OSError('broker kafka-1:9092 is unreachable')
connected: False
```

To samo sprzątanie następuje, gdy anulowane zostanie samo `connect()`. `ConnectError` dziedziczy po
`SystemExit`, więc aplikacja, która go nie przechwyci, kończy działanie — i zwykle właśnie tego chcesz,
gdy któraś zależność leży. Zamockowani klienci nie są łączeni i nie wpływają na warstwy.

### <a id="when-the-tree-cannot-be-built"></a>Gdy nie da się zbudować drzewa

Rozwiązywanie sprawdza każde `__init__` przed jego wywołaniem, więc klient, którego nie da się zbudować,
zgłasza błąd, zanim cokolwiek się połączy — z nazwą argumentu i ścieżką od klienta, o którego prosisz:

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

try:
    Dependencies().resolve(UserRepository)  # a type checker refuses this line, and so does the container
except InvalidSignatureError as exc:
    print(f"{type(exc).__name__}: {exc}")
```

```text
InvalidSignatureError: Argument "users" of "Profiles.__init__" is UserRepository, which is not a client (resolving Checkout -> Profiles)
CircularDependencyError: Circular dependency: Orders -> Payments -> Orders
InvalidSignatureError: UserRepository is not a client: subclass Client or NotSingletonClient
```

Argument `__init__` otrzymuje klienta, gdy jego adnotacja typu jest klientem. Każdy inny
argument musi mieć wartość domyślną, której biblioteka nie rusza. Te przypadki kończą się błędem `InvalidSignatureError`:

| Argument `__init__` bez wartości domyślnej | Komunikat                                          |
|--------------------------------------------|----------------------------------------------------|
| brak adnotacji typu                        | `has no type hint`                                 |
| typ, który nie jest klientem               | `is UserRepository, which is not a client`         |
| `Client \| None`                           | `is Postgres \| None, a client cannot be optional` |
| klient, tylko pozycyjny (`/`)              | `is positional-only, a client is passed by keyword` |

Klasa, która w ogóle nie jest klientem, zażądana przez `resolve()`, kończy się błędem `UserRepository is not a client: subclass Client or NotSingletonClient`, zanim cokolwiek zostanie zbudowane.

Klienci zależni od siebie nawzajem w cyklu kończą się błędem `CircularDependencyError`, podklasą
`InvalidSignatureError`, a adnotacja typu, której nie da się wyewaluować, np. klasa zdefiniowana wewnątrz
funkcji albo zaimportowana pod `TYPE_CHECKING` — błędem `InvalidSignatureError`, który mówi o tym wprost.
Gdy błąd pochodzi z `inject()`, ścieżka zaczyna się od funkcji:
`(resolving handler -> Checkout -> Profiles)`. W [workerze lub jobie](#workers-and-jobs)
każdy z tych błędów kończy uruchomienie kodem wyjścia `1`, zanim cokolwiek się połączy.

## <a id="the-container"></a>Kontener

`Dependencies` to kontener. `DI` to gotowa do użycia instancja globalna; utwórz własną,
gdy potrzebujesz izolacji, np. w testach.

| Metoda               | Opis                                                                    |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Buduje `cls` i jego drzewo zależności. Idempotentne dla `Client`.       |
| `inject(func)`       | Zwraca `functools.partial(func, ...)` z podpiętymi argumentami-klientami. Każdy argument `func` poza `*args` / `**kwargs` musi mieć adnotację typu. |
| `connect()`          | Wywołuje `connect()` na każdym rozwiązanym kliencie, warstwa po warstwie. |
| `disconnect()`       | Wywołuje `disconnect()` warstwa po warstwie w odwrotnej kolejności, a potem `flush()` na kontenerze. |
| `async with`         | `connect()` przy wejściu, `disconnect()` przy wyjściu.                  |
| `mock(cls, new=None)`| Rejestruje zamiennik dla `cls` (domyślnie mock z autospec) do następnego `flush()`. Trzeba wywołać przed rozwiązaniem `cls`. |
| `override(cls, new=None)` | Zamiennik na czas bloku `with`, a potem `flush()`; zob. [Testowanie](#testing). |
| `flush()`            | Zapomina wszystkich rozwiązanych klientów.                              |
| `timings`            | Po jednym `ClientTiming` na klienta ostatniego `connect()`; zob. [Czasy startu](#startup-timings). |
| `graph()`            | `Graph` rozwiązanych klientów z ich zależnościami i warstwami, wraz z `to_mermaid()`; zob. [Graf](#the-graph). |

Wynik `inject()` zachowuje typ zwracany funkcji, a jej pozostałe argumenty pozostają bez typów:
sprawdzacz typów nie potrafi odjąć argumentów-klientów od sygnatury.

`resolve`, `inject`, `mock`, `override` i `flush` działają tylko wtedy, gdy kontener jest rozłączony:
całe drzewo buduje się przed startem.

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: resolve(Cache): the container is already connected; ...
```

Kontener można bezpiecznie używać z kilku wątków: jedna blokada na kontener serializuje `resolve`, `inject`,
`mock`, `override` i `flush`, więc singleton zażądany przez dwa wątki naraz jest budowany raz.
`connect()` i `disconnect()` należą do jednej pętli zdarzeń.

```python
import threading

from nuke_di import Client, Dependencies


class Postgres(Client):
    instances = 0

    def __init__(self) -> None:
        type(self).instances += 1


class Orders(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


deps = Dependencies()
threads = [threading.Thread(target=deps.resolve, args=(Orders,)) for _ in range(8)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
print("instances:", Postgres.instances, "clients:", len(deps.connect_clients))
```

```text
instances: 1 clients: 2
```

## <a id="workers-and-jobs"></a>Workery i joby

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

### <a id="your-first-job"></a>Twój pierwszy job

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
INFO  nuke_di.core: Connected 4 clients in 1 layer in 0.00s (slowest: Warehouse 0.00s, Postgres 0.00s, Shutdown 0.00s)
postgres: upserted 3 rows into users
postgres: upserted 3 rows into orders
postgres: disconnected
warehouse: disconnected
INFO  nuke_di.run: Run app.jobs.sync.sync finished with exit code 0 in 0.002s
```

#### <a id="one-entrypoint-per-module-defined-last"></a>Jeden punkt wejścia na moduł, zdefiniowany na końcu

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

### <a id="parameters"></a>Parametry

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

#### <a id="supported-types"></a>Obsługiwane typy

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

#### <a id="parameters-in-tests"></a>Parametry w testach

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

### <a id="your-first-worker"></a>Twój pierwszy worker

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

Na Windowsie obsługiwany jest tylko SIGINT (Ctrl+C); SIGTERM zachowuje się domyślnie.

### <a id="grace-period"></a>Okres karencji

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
`SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers`. Przy wartościach domyślnych drzewo
z dwiema warstwami zużywa całe domyślne `terminationGracePeriodSeconds` Kubernetesa, czyli 30 sekund,
więc przy głębszych drzewach zmniejsz timeouty albo wydłuż okres karencji.

### <a id="background-tasks"></a>Zadania w tle

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

### <a id="exit-codes"></a>Kody wyjścia

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

### <a id="hooks"></a>Hooki

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

#### <a id="startup-metrics-and-structured-logs"></a>Metryki startu i logi strukturalne

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

| Pole       | Obecne w                                                                      |
|------------|-------------------------------------------------------------------------------|
| `run`      | Każdym rekordzie powstałym wewnątrz workera lub joba, także kontenera: nazwa uruchomienia |
| `client`   | Każdym rekordzie o jednym kliencie: rozwiązywanie, łączenie, rozłączanie, błędy |
| `layer`    | Każdym rekordzie o łączeniu lub rozłączaniu klienta oraz `Connecting layer`   |
| `duration` | Sekundy: połączony lub rozłączony klient, podsumowanie startu, zakończone uruchomienie |

Każde uruchomienie łączy też własnych klientów `Shutdown` i `BackgroundTasks`, więc pojawiają
się oni w `run.clients` i w podsumowaniu.

### <a id="running-in-kubernetes"></a>Uruchamianie w Kubernetes

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
      terminationGracePeriodSeconds: 30  # >= SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers
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

## <a id="fastapi"></a>FastAPI

Operacja ścieżki (path operation) w FastAPI przyjmuje klienta tak samo jak job — po adnotacji typu.
W handlerach nie trzeba pisać nic więcej: żadnego `Depends`, żadnego `inject()`.

```bash
pip install "nuke-di[fastapi]"
```

Wymaga FastAPI 0.105 lub nowszego. Przykłady korzystają ze wspólnego modułu klientów:

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

API:

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

Co się stało:

1. `setup(app)` sprawił, że każda trasa zadeklarowana potem na `app` wypełnia swoje argumenty-klientów
   z globalnego `DI`, i opakował lifespan aplikacji.
2. `@app.get` zauważył `users: UserService` i tylko to zanotował; przy imporcie nic nie zostało zbudowane.
3. Przy starcie lifespan rozwiązał klientów tras obsługiwanych przez aplikację — jej własnych i tych
   z dołączonych routerów — i połączył ich, warstwa po warstwie. Przy zamykaniu ich rozłączył.
4. Żądanie do `/users/42` dostało połączony `UserService`. `/me` przeszło przez zależność
   `current_user`, która w ten sam sposób przyjmuje `db: Database`.

Zasady:

- **Gdzie wstawiani są klienci.** W argumentach operacji ścieżki, endpointów websocket i każdej
  zależności, której używają, na dowolnej głębokości: funkcje oraz klasy użyte jako `Depends(Auth)`
  lub `Annotated[Auth, Depends()]`,
  łącznie z `dependencies=` trasy, jej routera, `include_router()` i aplikacji. Argument jest
  klientem, gdy jego adnotacja typu jest klientem, także wewnątrz `Annotated[UserService, ...]`
  bez `Depends`. Każdy inny argument należy do FastAPI: ścieżka, query, nagłówek, body, `Depends`.
- **Routery.** Twórz je przez `ClientRouter(...)`, który przyjmuje te same argumenty co `APIRouter`,
  i dołączaj do aplikacji albo do innego `ClientRouter`. `APIRouter(route_class=ClientRoute)`
  wystarczy dla routera, który nie dołącza innych routerów. Dla innego kontenera użyj
  `setup(app, container)` i `ClientRouter(container=container)`; dołączenie routera z innego
  kontenera od razu zgłasza `TypeError`.
- **Wywołaj `setup(app)` przed trasami.** Trasa z klientem zadeklarowana wcześniej od razu kończy się
  błędem `TypeError` opisanym [niżej](#not-supported).
- **Tylko to, co obsługuje aplikacja.** Router, którego aplikacja nie dołącza, np. importowany wyłącznie
  w teście, niczego nie łączy przy starcie aplikacji.
- **Instancje.** Tak jak w `inject()`, `Client` to jedna instancja na kontener, a
  `NotSingletonClient` — jedna instancja na każdy argument, który go deklaruje, a nie jedna na żądanie.
- **Lifespan.** Własny `lifespan=` aplikacji działa wewnątrz: jego kod startowy widzi połączonych klientów,
  a kod zamykający wykonuje się, zanim klienci się rozłączą. Przy zamykaniu ustawiany jest `Shutdown`
  i zatrzymywane są `BackgroundTasks`, jeśli aplikacja ich używa, zanim klienci się rozłączą — tak jak w
  workerze. `BackgroundTasks` z samego FastAPI to inna klasa i nie jest klientem.
- **Funkcja pozostaje funkcją.** Jej sygnatura pokazuje teraz FastAPI `Annotated[UserService, Depends(...)]`,
  ale bezpośrednie wywołanie z klientem, np. w teście jednostkowym, działa tak jak wcześniej.

**Testowanie.** Import aplikacji niczego nie buduje, więc test podmienia klienta, zanim `TestClient`
wystartuje aplikację — przez [`override()`](#testing) albo fixture `global_di`:

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

`app.dependency_overrides` nadal działa, także dla funkcji-zależności, która przyjmuje klientów.

**Websockety.** Endpoint websocket przyjmuje klientów w ten sam sposób, na aplikacji albo na `ClientRouter`:

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

**Klient, który nie może się połączyć**, przerywa start. Lifespan zgłasza zwykły `RuntimeError`
z przyczyną `ConnectError`, bo `SystemExit` wydostałby się poza pętlę zdarzeń serwera, a serwer
raportuje błąd i kończy działanie:

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

### <a id="not-supported"></a>Nieobsługiwane

W tych miejscach klienci nie są przyjmowani. Każde z nich zgłasza `TypeError` z wyjaśnieniem już przy deklaracji trasy:

| Miejsce                                                 | Zamiast tego                                  |
|---------------------------------------------------------|-----------------------------------------------|
| Router utworzony bez `ClientRouter` / `ClientRoute`     | Utwórz go przez `ClientRouter(...)`           |
| Endpoint websocket na `APIRouter(route_class=ClientRoute)` | Utwórz router przez `ClientRouter(...)` |
| Opcjonalny klient, `Database \| None`                   | Zwykły `Database`                             |
| Metoda związana lub obiekt wywoływalny jako endpoint lub zależność | Funkcja lub klasa                  |

W odróżnieniu od powyższych przypadków trasę routera dołączonego do zwykłego `APIRouter` zamiast do
`ClientRouter` wykrywają tylko starsze wersje FastAPI. W FastAPI 0.14x trasa zostaje zadeklarowana
i aplikacja startuje, ale jej żądania kończą się błędem
`RuntimeError: UserService was not started with the app: include the router of its route into
the app or into a ClientRouter, not into a plain APIRouter`.

Żądanie, które przyjdzie z pominięciem lifespan, np. przez `TestClient(app)` bez `with`, dostaje
`RuntimeError`: `UserService is not connected: start the app with its lifespan`.

## <a id="litestar"></a>Litestar

Handler trasy w Litestar także przyjmuje klienta po adnotacji typu — przez plugin:

```bash
pip install "nuke-di[litestar]"
```

Wymaga Litestar 2.15 lub nowszego. Z klientami z przykładów dla [FastAPI](#fastapi):

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

`ClientPlugin()` znalazł `users: UserService` w `get_user` oraz `db: Database` w zależności
`current_user`, przekazał obu klientów do Litestar jako zależności i połączył ich przy starcie.

Zasady:

- **Gdzie wstawiani są klienci.** W argumentach handlerów HTTP i `@websocket`, z którymi tworzona jest
  aplikacja, łącznie z handlerami routerów i kontrolerów na dowolnej głębokości, oraz każdej zależności
  zadeklarowanej na aplikacji, routerze, kontrolerze lub handlerze: funkcje i klasy.
- **Po nazwie.** Litestar dostarcza zależności po nazwie argumentu, więc nuke-di dostarcza każdy
  argument-klienta pod jego nazwą, na poziomie aplikacji. Jedna nazwa oznacza jednego klienta w całej
  aplikacji: `users: UserService` w jednym handlerze i `users: Billing` w innym zgłaszają `TypeError`
  przy tworzeniu aplikacji. Zależność o tej samej nazwie zadeklarowana przez aplikację, router,
  kontroler lub handler ma pierwszeństwo przed klientem.
- **Instancje.** `Client` to jedna instancja na kontener, a `NotSingletonClient` — jedna instancja na
  nazwę argumentu.
- **Lifespan.** Klienci łączą się, zanim wykonają się własne `lifespan=` i `on_startup=` aplikacji,
  i rozłączają się po jej hookach `on_shutdown=`, które Litestar wywołuje na końcu. `Shutdown` i
  `BackgroundTasks` działają tak jak w [FastAPI](#fastapi).
- **Funkcja pozostaje funkcją.** Jej argumenty-klienci mają teraz adnotację jawnych zależności Litestar,
  których wartość nie jest walidowana: `Annotated[UserService, Dependency(), SkipValidationMarker()]`,
  czego Litestar 2.23 wymaga zamiast zależności dopasowanej wyłącznie po nazwie. Bezpośrednie wywołanie
  funkcji działa tak jak wcześniej.
- **Pluginy.** Umieść `ClientPlugin()` za każdym pluginem, który dodaje handlery tras: plugin widzi te
  handlery, które aplikacja ma w chwili, gdy przychodzi jego kolej.
- **Inny kontener.** `ClientPlugin(container)`.

**Testowanie.** Tak jak w FastAPI, test podmienia klienta, zanim `TestClient` wystartuje aplikację:

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

**Nieobsługiwane.** Listener websocket, `@websocket_listener` lub klasa `WebsocketListener`, nie
przyjmuje klientów: Litestar odczytuje jego sygnaturę już przy deklaracji, zanim plugin ją zobaczy,
więc aplikacja zgłasza `TypeError` i wskazuje zamiast niego handler `@websocket`. Argument-klient
o nazwie zarezerwowanej przez Litestar, takiej jak `state` czy `request`, również powoduje `TypeError`.
Handler zarejestrowany po utworzeniu aplikacji, przez `app.register()`, nie jest widoczny.

## <a id="faststream"></a>FastStream

Subscriber w FastStream przyjmuje klienta po adnotacji typu, obok wiadomości:

```bash
pip install "nuke-di[faststream]"
```

Wymaga FastStream 0.6 lub nowszego, z dowolnym brokerem. Z klientami z przykładów dla [FastAPI](#fastapi):

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

Wiadomość została opublikowana tak:

```python
# publish.py
import asyncio

from faststream.nats import NatsBroker


async def main() -> None:
    async with NatsBroker("nats://localhost:4222") as broker:
        await broker.publish(42, "greetings")


asyncio.run(main())
```

Zasady:

- **Gdzie wstawiani są klienci.** W argumentach subscriberów brokerów aplikacji, także tych
  z dołączonych routerów, oraz każdego `Depends(...)`, którego używają, na dowolnej głębokości: funkcje
  i klasy, łącznie z `dependencies=` subscribera, jego routera i brokera. Każdy inny argument należy
  do FastStream: wiadomość, jej pola, `Context()`.
- **Którzy klienci startują.** Przy starcie — klienci każdego subscribera obsługiwanego przez brokery
  aplikacji, łącznie z routerami. Subscribery można deklarować przed `setup(app)` albo po nim.
- **Lifespan.** Klienci łączą się przed własnymi hookami `lifespan=` i `on_startup=` aplikacji i zanim
  wystartują brokery; rozłączają się, gdy brokery się zatrzymają, i po hookach `after_shutdown=`.
  `Shutdown` i `BackgroundTasks` działają tak jak w [FastAPI](#fastapi). `setup()` działa także na
  `AsgiFastStream`.
- **Instancje.** Tak jak w `inject()`, `Client` to jedna instancja na kontener, a
  `NotSingletonClient` — jedna instancja na każdy argument, który go deklaruje, a nie jedna na wiadomość.
- **Funkcja pozostaje funkcją.** Jej sygnatura pokazuje FastStream `Annotated[UserService, Depends(...)]`,
  tak jak w [FastAPI](#fastapi).
- **Jedna aplikacja naraz.** Funkcja-subscriber i jej zależności są przepisywane raz, niezależnie od
  kontenera, więc aplikacje, które je współdzielą, np. aplikacja na test z brokerem na poziomie modułu,
  działają jedna po drugiej: aplikacja, która startuje, gdy działa inna z tą samą funkcją, nie wystartuje.
  Funkcja-zależność, która przyjmuje klientów, obsługuje handlery albo FastAPI, albo FastStream, nie oba naraz.

**Testowanie.** Testowy broker FastStream nie uruchamia hooków aplikacji, więc wystartuj w nim aplikację
przez `TestApp`:

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

Wiadomość obsłużona z pominięciem lifespan aplikacji, np. przez `TestNatsBroker(broker)` bez `TestApp`,
zgłasza `RuntimeError: UserService is not connected: start the app with its lifespan`. Subscriber
dodany po starcie aplikacji zgłasza `RuntimeError: UserService was not started with the app`.

## <a id="testing"></a>Testowanie

**Klient przez kontener.** Zarejestruj mocki, zanim drzewo zostanie rozwiązane; każdy konsument
otrzyma wtedy mock:

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

**Klient na jeden blok, przez `override()`.** `override(cls, new=None)` rejestruje zamiennik
tak jak `mock()`, ale obowiązuje on do końca bloku `with`, nawet przez kilka cykli `async with`,
a przy wyjściu kontener jest czyszczony (`flush()`), więc nic, co z nim rozwiązano, nie przecieka
do następnego testu. Działa także z globalnym `DI`:

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

Testy asynchroniczne w tej sekcji używają [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) z
`asyncio_mode = auto` w `pytest.ini`; bez tego pytest nie uruchamia testów `async def`.

```console
$ pytest -q -s test_greet.py
Hello, alice!
after the block: OrderedDict()
.Hello, bob!
.
2 passed in 0.01s
```

`database: connected` nigdy się nie wypisuje: zamiennik nie jest łączony.

Zasady:

- **Podmieniaj, zanim rozwiążesz.** Zamiennik zarejestrowany po rozwiązaniu `cls` trafiłby
  tylko do konsumentów rozwiązanych później, a wcześniejsi zostaliby z prawdziwym klientem, dlatego
  `mock()` w takiej sytuacji zgłasza błąd:

  ```python
  DI.inject(handler)  # resolves UserService -> Database
  DI.mock(Database)  # ConnectError: Database is already resolved, call mock() before resolve() or inject()
  ```

- **`override()` zaczyna od kontenera bez rozwiązanych klientów.** W przeciwnym razie czyszczenie przy
  wyjściu po cichu porzuciłoby to, co rozwiązano przed blokiem, więc zgłaszany jest
  `ConnectError: override(Database) needs a container without resolved clients, found: Database, UserService`.
  Najpierw wywołaj `DI.flush()` albo użyj opisanego niżej fixture'a `global_di`.
- **Jeden zamiennik na klasę.** Ponowne wywołanie `mock(cls)` zwraca zamiennik, który jest już
  zarejestrowany; `mock(cls, other)` i `override(cls)` zgłaszają `ConnectError: Database already has a
  replacement`.
- **Zamienniki nie są łączone.** Ich `connect()` / `disconnect()` nigdy nie są wywoływane i nie
  biorą one udziału w [warstwach](#layers).
- **Jak długo żyje zamiennik.** Zamiennik z `mock()` znika przy następnym `flush()`, także tym
  na końcu `disconnect()`: test, który łączy kontener więcej niż raz, powinien użyć
  `override()`, którego zamiennik przetrwa każde `flush()` aż do końca bloku. Wyjątek wewnątrz
  bloku propaguje się bez zmian; zwykłe wyjście z bloku, gdy kontener jest wciąż połączony,
  zgłasza `ConnectError`.
- **Zagnieżdżanie.** Bloki dla różnych klas można zagnieżdżać, o ile każdy z nich otwiera się, zanim
  cokolwiek zostanie rozwiązane, np. `with DI.override(Database), DI.override(Clock):`; wyjście z bloku
  wewnętrznego zachowuje zamiennik z zewnętrznego.

**Fixture'y pytest.** Instalacja `nuke-di` rejestruje plugin pytest z dwoma fixture'ami. Żaden z nich
nie jest autouse, więc istniejące testy działają dokładnie tak jak wcześniej:

| Fixture     | Daje                                                   |
|-------------|--------------------------------------------------------|
| `di`        | Świeży `Dependencies` na jeden test                    |
| `global_di` | Globalny `DI`, czyszczony przed testem i po nim        |

Test, który zostawi kontener połączony, dostaje błąd w teardown, a kontener i tak zostaje
wyczyszczony, więc następny test zaczyna od zera:

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

Fixture'y nie mogą same rozłączyć zapomnianego kontenera: w chwili teardown pętla zdarzeń testu
może być już zamknięta. `global_di` chroni tylko te testy, które go żądają: test, który używa globalnego
`DI` bez niego, nadal może zostawić klientów następnemu testowi. Projekt, który definiuje własny
fixture `di`, zachowuje go, bo fixture z `conftest.py` ma pierwszeństwo przed fixture'em z pluginu;
`pytest -p no:nuke_di` wyłącza plugin.

**Job bezpośrednio.** Import modułu nie uruchamia joba, więc wywołaj funkcję z mockami
i parametrami:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    pg.upsert.assert_awaited_once_with("users", ["row"])
```

**Job przez kontener**, z klientami spiętymi tak jak na produkcji:

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

**Każdy punkt wejścia się rozwiązuje.** Import modułu nie uruchamia jego joba ani workera, a `inject()`
buduje drzewo, niczego nie łącząc, więc jeden test sprawdza okablowanie wszystkich punktów wejścia w CI:
cykl, argument bez adnotacji typu, wymagany argument, który nie jest klientem, albo `__init__`, który
rzuca wyjątek, wywalają go z tym samym błędem, jaki wypisałoby prawdziwe uruchomienie, i baza danych nie
jest potrzebna:

```python
# test_wiring.py
from collections.abc import Callable

import pytest

from nuke_di import Dependencies

from app.jobs import sync
from app.workers import consumer


@pytest.mark.parametrize("entrypoint", [sync.sync, consumer.consumer])
def test_entrypoint_resolves(entrypoint: Callable[..., object]) -> None:
    Dependencies().inject(entrypoint)  # runs every __init__, connects nothing
```

```console
$ pytest -q test_wiring.py
..                                                                       [100%]
2 passed in 0.05s
```

Zachowaj kontener, aby dostać [graf](#the-graph) punktu wejścia do jego README:
`deps = Dependencies(); deps.inject(sync.sync); print(deps.graph().to_mermaid())`.

**Worker.** `Shutdown.set()` robi to samo, co zrobiłby SIGTERM:

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

## <a id="configuration"></a>Konfiguracja

| Zmienna środowiskowa         | Domyślnie | Opis                                             |
|------------------------------|-----------|--------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`      | Timeout `connect()` pojedynczego klienta, w sekundach |
| `CONNECT_CONCURRENCY`        | `0`       | Ilu klientów może jednocześnie łączyć się lub rozłączać w całym kontenerze; `0` oznacza brak limitu |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`      | Timeout `disconnect()` pojedynczego klienta, w sekundach |
| `SHUTDOWN_GRACE_SECONDS`     | `10`      | Jak długo worker lub job może jeszcze działać po SIGTERM / SIGINT, zanim zostanie anulowany, w sekundach; odczytywane przy starcie procesu |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

Ustawienia kontenera są odczytywane w chwili tworzenia instancji `Dependencies`. Można je też
przekazać jawnie:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```

## <a id="errors"></a>Błędy

| Wyjątek                     | Zgłaszany, gdy                                            |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | `__init__` klienta zgłosił wyjątek                        |
| `ConnectError`              | `connect()` klienta zgłosił wyjątek albo kontener jest w niewłaściwym stanie (np. rozwiązywanie po połączeniu, mockowanie już rozwiązanego klienta, `override()` na kontenerze z rozwiązanymi klientami) |
| `ConnectTimeoutError`       | `connect()` klienta przekroczył `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError`     | `__init__` klienta ma wymagany argument, który nie jest klientem, `inject()` dostał funkcję z argumentem bez adnotacji typu, `resolve()` dostał klasę, która nie jest klientem, albo parametr punktu wejścia ma nieobsługiwany typ lub flagę, która z czymś koliduje; zob. [Gdy nie da się zbudować drzewa](#when-the-tree-cannot-be-built) |
| `CircularDependencyError`   | Klienci zależą od siebie nawzajem w cyklu; podklasa `InvalidSignatureError` |
| `UsageError`                | Wiersz poleceń workera lub joba nie pasuje do jego parametrów; zapisywany jako `Run.error`, kod wyjścia `2` |

`InitializeDependencyError` i `ConnectError` dziedziczą po `SystemExit`: aplikacja, której
zależności nie mogą wystartować, powinna się zatrzymać. Przechwyć je jawnie, jeśli potrzebujesz
innego zachowania; pierwotny wyjątek jest dostępny jako `__cause__`.

`nuke-di` loguje przez standardowy moduł `logging` w loggerze `nuke_di`, z
[polami strukturalnymi](#startup-metrics-and-structured-logs) dla potoków logów.

## <a id="performance"></a>Wydajność

`nuke-di` jest mierzony, a nie strojony na wyczucie. `benchmarks/run.py` mierzy, co sama biblioteka
dokłada na pustych klientach: `resolve()` szerokich, głębokich i mieszanych drzew z 10, 100 i 1000
klientów, narzut planowania `connect()` i `disconnect()` ponad własne korutyny klientów, `inject()`,
`NotSingletonClient`, cykl `mock()` / `override()` w teście, jedno żądanie FastAPI, czas importu i pamięć.
Wypisuje tabelę Markdown z medianą i p95 powtórzeń oraz liczbą na jednego klienta:

```console
$ uv run python benchmarks/run.py --only resolve --size 100
nuke-di 1.11.1 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit d8dc493 · N = 100 · 20 repeats

| Scenario                                         | Shape          |   N |  Median |     p95 | Per client |
|--------------------------------------------------|----------------|----:|--------:|--------:|-----------:|
| resolve(), cold                                  | wide           | 100 |  391 µs |  429 µs |    3.91 µs |
| resolve(), second container, classes seen before | wide           | 100 |  136 µs |  192 µs |    1.36 µs |
| resolve(), warm                                  | wide           | 100 |  157 ns |  165 ns |            |
| resolve(), cold                                  | deep           | 100 |  416 µs |  541 µs |    4.16 µs |
| resolve(), second container, classes seen before | deep           | 100 |  127 µs |  133 µs |    1.27 µs |
| resolve(), warm                                  | deep           | 100 |  157 ns |  162 ns |            |
| resolve(), cold                                  | mixed          | 100 |  509 µs |  607 µs |    5.09 µs |
| resolve(), second container, classes seen before | mixed          | 100 |  153 µs |  182 µs |    1.53 µs |
| resolve(), warm                                  | mixed          | 100 |  158 ns |  169 ns |            |
```

`--size N` i `--repeat K` ustawiają rozmiar drzewa i liczbę powtórzeń, `--only` wybiera scenariusz
(`resolve`, `connect`, `inject`, `not_singleton`, `overrides`, `fastapi`, `import`, `memory`), a
`--json PATH` zapisuje liczby razem z wersją Pythona, platformą i commitem do późniejszego porównania.
[docs/benchmarks.md](../benchmarks.md) objaśnia każdy scenariusz i zapisuje punkt odniesienia dla Pythona
3.11–3.14, zmierzony na Apple M2 Pro z `nuke-di` 1.11.1: `resolve()` kosztuje 4–6,5 µs na klienta, więc
drzewo z 1000 klientów powstaje w mniej niż 6 ms; drugi kontener w procesie, czyli to, co płaci każdy test
po pierwszym, rozwiązuje te same klasy według liczby z cache, 1,2–2,2 µs na klienta, także z adnotacjami
w postaci napisów; `connect()` dokłada 10–14 µs na klienta w warstwie i około 0,1 ms na warstwę; handler
FastAPI, który dostaje klienta przez `nuke-di`, kosztuje tyle samo co handler ze zwykłym `Depends()`;
`import nuke_di` trwa 29–41 ms, głównie przez `asyncio`. CI uruchamia zestaw jako test
dymny, bez progu: runner GitHuba jest zbyt hałaśliwy, by na nim blokować.

`benchmarks/compare.py` przepuszcza te same drzewa przez dishka, wireup, dependency-injector i injector,
rejestrując te same klasy tak, jak robi to każda biblioteka: zimny kontener z rozwiązanym korzeniem na
klasach nowych dla procesu, ponowne pobranie korzenia i jedno żądanie FastAPI przez integrację każdej
biblioteki. Biblioteki są w
grupie zależności `compare`:

```console
$ uv run python benchmarks/compare.py --size 100 --summary
nuke-di 1.11.1 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit d8dc493 · N = 100 · 20 repeats
nuke-di 1.11.1 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

| Lower is better                                          | nuke-di        | dishka          | wireup          | dependency-injector | injector        |
|----------------------------------------------------------|---------------:|----------------:|----------------:|--------------------:|----------------:|
| Cold start: a container and a tree of 100 clients        | **563 µs**     | 13.2 ms (23.5×) | 20.7 ms (36.7×) | 1.11 ms (2.0×)      | 1.40 ms (2.5×)  |
| Cold start: the same 100 clients with string annotations | 1.29 ms (1.1×) | 14.2 ms (12.6×) | 22.4 ms (19.8×) | **1.13 ms**         | 1.41 ms (1.2×)  |
| A cached root                                            | 157 ns (4.2×)  | 266 ns (7.1×)   | 94.1 ns (2.5×)  | **37.6 ns**         | 1.20 µs (31.8×) |
| A FastAPI request with a client                          | **103 µs**     | 104 µs (1.0×)   | 218 µs (2.1×)   | 215 µs (2.1×)       | —               |
```

![nuke-di against other DI libraries: lower is better](../benchmarks/compare.png)

Czy więc `nuke-di` jest najszybszy? Przy budowaniu drzewa z prawdziwymi adnotacjami typów i w żądaniu
FastAPI tak: dependency-injector i injector budują drzewo 2–2,5 razy dłużej, dishka i wireup 23–37 razy
dłużej przez walidację grafu przy tworzeniu kontenera, a wireup i dependency-injector płacą dwa razy więcej
na każde żądanie. Z adnotacjami w postaci napisów dependency-injector, który nie czyta żadnych adnotacji,
jest 1,1 raza szybszy. Na korzeniu z cache wygrywa `get()` dependency-injector napisany w Cythonie, o około
120 ns, i wireup o 60 ns, czego żadna aplikacja nie zauważy. Pełna tabela z metodą jest w
[docs/benchmarks.md](../benchmarks.md#comparison-with-other-libraries).

## <a id="development"></a>Rozwój

```bash
make install   # uv sync --locked
make check     # ruff, mypy, pyright and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

Pokrycie linii i gałęzi wynosi 100%, a CI kończy się błędem, jeśli spadnie poniżej tego poziomu
(`fail_under = 100` w `pyproject.toml`).

### <a id="releases"></a>Wydania

Każdy merge do `master` to nowe wydanie. Workflow `Release` publikuje w PyPI wersję z
`pyproject.toml`, oznacza ją tagiem `vX.Y.Z` i tworzy release na GitHubie z odpowiadającej jej sekcji
`CHANGELOG.md`. Dlatego każdy pull request niesie własną wersję: podnieś ją przez `uv version --bump
patch|minor|major` i zamień `## [Unreleased]` na `## [X.Y.Z] - YYYY-MM-DD`, dodając na końcu pliku
link do porównania. CI sprawdza to w każdym pull requeście, a lokalnie robi to `make check-version`:

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

Zmiana, która trafi do `master` bez nowej wersji, np. wypchnięta bezpośrednio, kończy workflow
`Release` błędem, zanim cokolwiek zostanie zbudowane lub opublikowane.

## <a id="license"></a>Licencja

[MIT](../../LICENSE)
