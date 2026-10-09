# <a id="testing"></a>Testowanie

[English](../../guide/testing.md) · [Русский](../ru/testing.md) · [简体中文](../zh-CN/testing.md) · [Español](../es/testing.md) · [Português (Brasil)](../pt-BR/testing.md) · [日本語](../ja/testing.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

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
  biorą one udziału w [kolejności łączenia](clients.md#connect-order).
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

[Wtyczka mypy](clients.md#checking-the-tree-with-mypy) zgłasza te same błędy sygnatur i cykle, niczego
nie uruchamiając; test dodatkowo wywołuje każde `__init__`, więc wyłapuje też takie, które rzuca wyjątek.

Zachowaj kontener, aby dostać [graf](clients.md#the-graph) punktu wejścia do jego README:
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
