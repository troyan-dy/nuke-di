# <a id="fastapi"></a>FastAPI

[English](../../guide/fastapi.md) · [Русский](../ru/fastapi.md) · [简体中文](../zh-CN/fastapi.md) · [Español](../es/fastapi.md) · [Português (Brasil)](../pt-BR/fastapi.md) · [日本語](../ja/fastapi.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

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
   z dołączonych routerów — i połączył każdego po jego zależnościach. Przy zamykaniu ich
   rozłączył.
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
- **Jeden kontener na aplikację.** Każda aplikacja dostaje klientów kontenera przekazanego do jej `setup()`,
  więc dwie aplikacje na dwóch kontenerach jednocześnie obsługują te same funkcje, np. w testach. Aplikacja
  zamontowana w aplikacji z `setup()` albo taka, która obsługuje jej trasy, np. przez
  `include_router(api.router)`, który uruchamia lifespan `api`, dostaje klientów uruchomionych przez tamtą
  aplikację.
- **Instancje.** Tak jak w `inject()`, `Client` to jedna instancja na kontener, a
  `NotSingletonClient` — jedna instancja na każdy argument, który go deklaruje, a nie jedna na żądanie.
- **Lifespan.** Własny `lifespan=` aplikacji działa wewnątrz: jego kod startowy widzi połączonych klientów,
  a kod zamykający wykonuje się, zanim klienci się rozłączą. Przy zamykaniu ustawiany jest `Shutdown`
  i zatrzymywane są `BackgroundTasks`, jeśli aplikacja ich używa, zanim klienci się rozłączą — tak jak w
  workerze. `BackgroundTasks` z samego FastAPI to inna klasa i nie jest klientem.
- **Funkcja pozostaje funkcją.** Jej sygnatura pokazuje teraz FastAPI `Annotated[UserService, Depends(...)]`,
  ale bezpośrednie wywołanie z klientem, np. w teście jednostkowym, działa tak jak wcześniej.

**Testowanie.** Import aplikacji niczego nie buduje, więc test podmienia klienta, zanim `TestClient`
wystartuje aplikację — przez [`override()`](testing.md) albo fixture `global_di`:

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

## <a id="not-supported"></a>Nieobsługiwane

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

## <a id="class-based-views"></a>Widoki oparte na klasach

Trasy, które potrzebują tego samego — użytkownika żądania i kilku klientów — przyjmują to jako jedną
klasę. Ta klasa jest zależnością FastAPI z `__init__` z adnotacjami typów, napisaną tak jak klient,
a jej `__init__` przyjmuje dane żądania i klientów obok siebie:

```python
# app/views.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di.fastapi import setup

app = FastAPI()
setup(app)


class Account:
    # Built by FastAPI for every request, from a header and two clients
    def __init__(self, x_user_id: Annotated[int, Header()], db: Database, users: UserService) -> None:
        self.user_id = x_user_id
        self.db = db
        self.users = users

    async def name(self) -> str:
        return await self.db.fetch_user(self.user_id)

    async def greeting(self) -> str:
        return await self.users.greet(self.user_id)


CurrentAccount = Annotated[Account, Depends()]


@app.get("/me")
async def me(account: CurrentAccount) -> str:
    return await account.name()


@app.get("/me/greeting")
async def greeting(account: CurrentAccount) -> str:
    return await account.greeting()
```

```console
$ uvicorn app.views:app
INFO:     Started server process [50908]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51798 - "GET /me HTTP/1.1" 200 OK
INFO:     127.0.0.1:51800 - "GET /me/greeting HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [50908]
```

```console
$ curl localhost:8000/me -H "X-User-Id: 7"
"user-7"
$ curl localhost:8000/me/greeting -H "X-User-Id: 7"
"Hello, user-7!"
```

Test podmienia klienta raz dla wszystkich tras, które używają tej klasy:

```python
# tests/test_views.py
from fastapi.testclient import TestClient

from app.clients import Database
from app.views import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_account() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/me", headers={"X-User-Id": "7"}).json() == "alice"
        assert client.get("/me/greeting", headers={"X-User-Id": "7"}).json() == "Hello, alice!"
```

```console
$ pytest -q tests/test_views.py
.                                                                        [100%]
1 passed in 0.18s
```

Zasady:

- **Jeden widok na żądanie, jeden klient na kontener.** FastAPI buduje `Account` dla każdego żądania;
  `db` i `users` w nim to połączeni klienci kontenera, te same obiekty w każdym żądaniu. nuke-di
  przepisał sygnaturę klasy, tak jak robi to dla funkcji-zależności, a jej `__init__` zostawił bez
  zmian: `Account(x_user_id=7, db=db, users=users)` w teście jednostkowym działa tak jak wcześniej.
  Dataclass działa tak samo — jego pola są argumentami.
- **Widok, który nie potrzebuje niczego z żądania, jest klientem.** `class Account(Client)` przyjęty
  jako `account: Account` jest budowany raz na kontener i łączy się razem z pozostałymi. Natomiast
  klasę klienta zapisaną jako `Annotated[Account, Depends()]`, np. udekorowaną `@client_dataclass`,
  FastAPI buduje dla każdego żądania, a jej `connect()` nigdy się nie wykonuje: pomiń `Depends()`.
- **`@cbv` z fastapi-utils nie jest potrzebny.** Deklaruje on trasy ponownie na własnym, zwykłym
  `APIRouter`, więc atrybut klasy z typem klienta kończy się w `include_router()` błędem
  `TypeError: Database is a nuke-di client, not a pydantic type`. Klasa powyżej współdzieli klientów
  między trasami, korzystając wyłącznie z FastAPI.

## <a id="an-app-per-test-container"></a>Aplikacja na kontener testu

Fabryka aplikacji buduje aplikację wokół przekazanego jej kontenera, więc każdy test działa na
własnym kontenerze, a serwer — na globalnym `DI`. Funkcje pozostają na poziomie modułu:

```python
# app/factory.py
from typing import Annotated

from fastapi import Depends, FastAPI, Header

from app.clients import Database, UserService
from nuke_di import DI, Dependencies
from nuke_di.fastapi import ClientRouter, setup


async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def current_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user


def make_app(container: Dependencies) -> FastAPI:
    app = FastAPI()
    setup(app, container)
    app.add_api_route("/users/{user_id}", get_user)

    # A router fills clients from one container, so every app creates its own
    account = ClientRouter(prefix="/me", container=container)
    account.add_api_route("", me)
    app.include_router(account)
    return app


def create_app() -> FastAPI:
    # For the server: `uvicorn --factory app.factory:create_app`
    return make_app(DI)
```

```console
$ uvicorn --factory app.factory:create_app
INFO:     Started server process [50968]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51815 - "GET /users/42 HTTP/1.1" 200 OK
INFO:     127.0.0.1:51817 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [50968]
```

Testy budują aplikację na fixture `di`:

```python
# tests/test_factory.py
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.clients import Database
from app.factory import current_user, make_app
from nuke_di import Dependencies


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


@pytest.fixture
def app(di: Dependencies) -> Iterator[FastAPI]:
    # `di` is a fresh container for every test, a fixture of nuke-di
    with di.override(Database, FakeDatabase()):
        yield make_app(di)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_get_user(client: TestClient) -> None:
    assert client.get("/users/1").json() == "Hello, alice!"


def test_me(client: TestClient) -> None:
    assert client.get("/me", headers={"X-User-Id": "7"}).json() == "alice"


def test_dependency_overrides(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[current_user] = lambda: "carol"
    assert client.get("/me").json() == "carol"
```

```console
$ pytest -q tests/test_factory.py
...                                                                      [100%]
3 passed in 0.16s
```

Zasady:

- **Jeden `override()` dla całej aplikacji.** `di.override(Database, FakeDatabase())` podmienia bazę
  danych dla zależności `current_user`, dla `UserService` i dla wszystkiego innego, co przyjmuje
  `Database`, podczas gdy samo FastAPI wymaga osobnego wpisu w `app.dependency_overrides` dla każdej
  funkcji-zależności.
- **Funkcja na kilku kontenerach.** `get_user` i `current_user` są przepisywane raz; każda aplikacja przy
  starcie rozwiązuje ich klientów we własnym kontenerze, a żądanie dostaje klientów aplikacji, do której
  trafiło. Aplikacje zbudowane na różnych kontenerach obsługują te same funkcje, jedna po drugiej albo
  jednocześnie.
- **Router na aplikację.** `ClientRouter` wypełnia klientów z jednego kontenera; aplikacja, która
  dołącza router innego kontenera, zgłasza `TypeError: the router fills clients from another container than
  this app`. Twórz routery wewnątrz fabryki.
- **`app.dependency_overrides`** należy do jednej aplikacji i nadal działa, także dla zależności, która
  przyjmuje klientów, jak `current_user` powyżej.

## <a id="strawberry-graphql"></a>Strawberry GraphQL

Router FastAPI ze Strawberry, `GraphQLRouter`, jest `APIRouter`, a jego trasy budują kontekst GraphQL
przez zależność FastAPI. Kontekst jest więc klasą z `__init__` z adnotacjami typów, który przyjmuje
klientów, a resolvery odczytują ich z `info.context`:

```bash
pip install "nuke-di[fastapi]" strawberry-graphql
```

```python
# app/graphql.py
from collections.abc import AsyncIterator

import strawberry
from fastapi import FastAPI
from strawberry.fastapi import BaseContext, GraphQLRouter

from app.clients import UserService
from nuke_di.fastapi import ClientRoute, setup


class Context(BaseContext):
    # Built by FastAPI for every request, as a dependency of Strawberry's routes
    def __init__(self, users: UserService) -> None:
        super().__init__()
        self.users = users


@strawberry.type
class Query:
    @strawberry.field
    async def greeting(self, info: strawberry.Info[Context], user_id: int) -> str:
        return await info.context.users.greet(user_id)


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def greetings(self, info: strawberry.Info[Context], user_ids: list[int]) -> AsyncIterator[str]:
        for user_id in user_ids:
            yield await info.context.users.greet(user_id)


schema = strawberry.Schema(query=Query, subscription=Subscription)

app = FastAPI()
setup(app)
graphql = GraphQLRouter(schema, context_getter=Context, route_class=ClientRoute)
app.include_router(graphql, prefix="/graphql")
```

```console
$ uvicorn app.graphql:app
INFO:     Started server process [61129]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:51979 - "POST /graphql HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [61129]
```

```console
$ curl localhost:8000/graphql -H 'Content-Type: application/json' -d '{"query": "{ greeting(userId: 42) }"}'
{"data":{"greeting":"Hello, user-42!"}}
```

Zapytanie oraz subskrypcja przez websocket tego samego routera:

```python
# tests/test_graphql.py
from fastapi.testclient import TestClient

from app.clients import Database
from app.graphql import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_query() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        response = client.post("/graphql", json={"query": "{ greeting(userId: 1) }"})

    assert response.json() == {"data": {"greeting": "Hello, alice!"}}


def test_subscription() -> None:
    query = "subscription { greetings(userIds: [1, 2]) }"
    with (
        TestClient(app) as client,
        client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as ws,
    ):
        ws.send_json({"type": "connection_init"})
        assert ws.receive_json() == {"type": "connection_ack"}
        ws.send_json({"id": "1", "type": "subscribe", "payload": {"query": query}})
        assert ws.receive_json()["payload"] == {"data": {"greetings": "Hello, user-1!"}}
        assert ws.receive_json()["payload"] == {"data": {"greetings": "Hello, user-2!"}}
        assert ws.receive_json() == {"id": "1", "type": "complete"}
```

```console
$ pytest -q tests/test_graphql.py
..                                                                       [100%]
2 passed in 0.21s
```

Zasady:

- **Kontekst przyjmuje klientów, resolvery przyjmują kontekst.** FastAPI buduje `Context` dla każdego
  żądania i każdego połączenia websocket, z połączonymi klientami kontenera w środku;
  `strawberry.Info[Context]` przekazuje resolverom jego typ. Resolver nie przyjmuje klienta po
  adnotacji typu: Strawberry nie ma własnego wstrzykiwania zależności, a `info.context` to sposób,
  w jaki przekazuje wartości w dół.
- **`route_class=ClientRoute`** sprawia, że trasy routera wypełniają klientów, jak w każdym `APIRouter`.
  Strawberry przekazuje getter kontekstu do FastAPI opakowany we własną zależność, a nuke-di podąża przez
  nią aż do `Context`.
- **Subskrypcje.** FastAPI buduje trasę websocket routera bez klasy trasy, jak każdy websocket na
  `APIRouter`, a mimo to dostaje ona `Context` z klientami: współdzieli zależność kontekstu Strawberry
  z trasami GET i POST, które router deklaruje wcześniej przez `ClientRoute`. To one przepisują `Context`
  i włączają jego klientów do startu aplikacji. Własny endpoint websocket na takim routerze nadal zgłasza
  `TypeError`, zob. [Nieobsługiwane](#not-supported).
- **Inny kontener**: `route_class=app.router.route_class`, po `setup(app, container)`.
- Kontroler Litestar ze Strawberry przyjmuje klientów przez `ClientPlugin`, zob.
  [Litestar](litestar.md#strawberry-graphql).
