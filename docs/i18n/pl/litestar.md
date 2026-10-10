# <a id="litestar"></a>Litestar

[English](../../guide/litestar.md) · [Русский](../ru/litestar.md) · [简体中文](../zh-CN/litestar.md) · [Español](../es/litestar.md) · [Português (Brasil)](../pt-BR/litestar.md) · [日本語](../ja/litestar.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

Handler trasy w Litestar także przyjmuje klienta po adnotacji typu — przez plugin:

```bash
pip install "nuke-di[litestar]"
```

Wymaga Litestar 2.15 lub nowszego. Z klientami z przykładów dla [FastAPI](fastapi.md):

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
  `BackgroundTasks` działają tak jak w [FastAPI](fastapi.md).
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

## <a id="differences-from-fastapi"></a>Różnice względem FastAPI

Handler pisze się w obu tak samo: `users: UserService`. Różnice wynikają z tego, jak każdy framework
wstrzykuje zależności: FastAPI odczytuje `Depends` w sygnaturze każdej funkcji, a Litestar dopasowuje
zależność po nazwie argumentu, więc `ClientPlugin` dostarcza każdego klienta pod nazwą jego argumentu,
na poziomie aplikacji ([ADR-0004](../../adr/0004-litestar-clients-by-name.md)).

| | FastAPI | Litestar |
|---|---|---|
| Konfiguracja | `setup(app)` przed trasami; routery przez `ClientRouter` | `ClientPlugin()` w `plugins=`; dowolny router lub kontroler |
| Widoczne handlery | Każda trasa zadeklarowana po `setup(app)`, na aplikacji lub na dołączonym `ClientRouter` | Handlery, z którymi tworzona jest aplikacja; handler dodany później przez `app.register()` nie jest widoczny |
| Nazwy argumentów | Dowolne: `users: UserService` tutaj i `users: Billing` tam | Jedna nazwa, jeden klient w całej aplikacji; dwaj klienci pod jedną nazwą zgłaszają `TypeError` przy tworzeniu aplikacji |
| `NotSingletonClient` | Jedna instancja na argument | Jedna instancja na nazwę argumentu, współdzielona przez każdy handler, który używa tej nazwy |
| Co zmienia się w funkcji | Jej `__signature__`; `get_type_hints()` nadal pokazuje `UserService` | Jej `__annotations__`; `get_type_hints(include_extras=True)` pokazuje `Annotated[UserService, Dependency(), SkipValidationMarker()]` |
| Websockety | Endpointy `@app.websocket` | Handlery `@websocket`; listener websocket zgłasza `TypeError` |
| Własny lifespan aplikacji | Działa wewnątrz: klienci łączą się przed nim i rozłączają po nim | Klienci łączą się przed `lifespan=` i `on_startup=`, a rozłączają po `on_shutdown=` |
| Podmiana zależności w teście | `override()` albo `app.dependency_overrides` | `override()` albo zależność o tej samej nazwie na którejś warstwie, która ma pierwszeństwo przed klientem |
| Inny kontener | `setup(app, container)` i `ClientRouter(container=container)` | `ClientPlugin(container)` |

## <a id="litestar-3"></a>Litestar 3

Litestar 3 nie został jeszcze wydany: na dzień 2026-10-10 najnowsze wydanie w PyPI to 2.24.0
z 2026-06-11 i nie ma żadnego wydania wstępnego 3.0. Wiadomo o nim dwie rzeczy, a nuke-di niczego nie
zmienia, dopóki się nie ukaże:

- **Zależności wnioskowane znikają.** Litestar 2.24 ostrzega o zależności dopasowanej wyłącznie po
  nazwie: `Inferred dependencies will stop working in Litestar 3.0`. Adnotacja, którą zapisuje nuke-di,
  `Annotated[UserService, Dependency(), SkipValidationMarker()]`, jest tym, co oznaczają
  `NamedDependency[...]` i `SkipValidation[...]`, czyli formą jawną, i Litestar 2.24 nie ostrzega
  o żadnej jej części.
- **Planowane jest wstrzykiwanie po typie.** [Zapowiedź v3](https://litestar.dev/blog/v3-announcement)
  z 2026-07-26 przewiduje `TypeDependency[SomeService]` obok `NamedDependency`, z providerem
  kluczowanym typem, `dependencies={SomeService: provide_some_service}`, i wskazuje tę przebudowę DI
  jako funkcję, która wciąż dzieli 3.0 od wersji beta.

Gdy providery będą kluczowane typem, `ClientPlugin` mógłby dostarczać każdego klienta pod jego klasą
zamiast pod nazwą argumentu, a wiersze powyższej tabeli dotyczące nazw i instancji przestałyby
obowiązywać. Czy tak się stanie, zostanie rozstrzygnięte po ukazaniu się 3.0 i jego API, przez
ponowne rozpatrzenie ADR-0004. Handlery w żadnym przypadku się nie zmieniają: deklarują
`users: UserService`, a tylko plugin decyduje, jak przekazać to Litestar.

## <a id="strawberry-graphql"></a>Strawberry GraphQL

Kontroler Litestar ze Strawberry buduje kontekst GraphQL przez zależność Litestar, więc getter
kontekstu przyjmuje klientów jak każda inna zależność, a resolvery odczytują ich z `info.context`:

```bash
pip install "nuke-di[litestar]" strawberry-graphql
```

```python
# app/litestar_graphql.py
import strawberry
from litestar import Litestar
from strawberry.litestar import BaseContext, make_graphql_controller

from app.clients import UserService
from nuke_di.litestar import ClientPlugin


class Context(BaseContext, kw_only=True):
    users: UserService


async def get_context(users: UserService) -> Context:
    return Context(users=users)


@strawberry.type
class Query:
    @strawberry.field
    async def greeting(self, info: strawberry.Info[Context], user_id: int) -> str:
        return await info.context.users.greet(user_id)


schema = strawberry.Schema(query=Query)
GraphQLController = make_graphql_controller(schema, path="/graphql", context_getter=get_context)
app = Litestar([GraphQLController], plugins=[ClientPlugin()])
```

```console
$ uvicorn app.litestar_graphql:app
INFO:     Started server process [56803]
INFO:     Waiting for application startup.
database: connected
INFO - 2026-10-10 18:40:33,849 - nuke_di.core - core - Connected 2 clients in 0.00s (slowest: Database 0.00s, UserService 0.00s)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:53760 - "POST /graphql HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [56803]
```

```console
$ curl localhost:8000/graphql -H 'Content-Type: application/json' -d '{"query": "{ greeting(userId: 42) }"}'
{"data":{"greeting":"Hello, user-42!"}}
```

```python
# tests/test_litestar_graphql.py
from litestar.testing import TestClient

from app.clients import Database
from app.litestar_graphql import app
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


def test_query() -> None:
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        response = client.post("/graphql", json={"query": "{ greeting(userId: 1) }"})

    assert response.json() == {"data": {"greeting": "Hello, alice!"}}
```

```console
$ pytest -q -W ignore::DeprecationWarning tests/test_litestar_graphql.py
.                                                                        [100%]
1 passed in 0.37s
```

Zasady:

- **Getter kontekstu jest funkcją.** `BaseContext` Strawberry dla Litestar to `Struct` z msgspec;
  przekazany bezpośrednio jako `context_getter=Context` sprawia, że każde żądanie kończy się błędem
  `msgspec.ValidationError`.
- **Subskrypcje** działają na handlerze websocket tego samego kontrolera, który `ClientPlugin` również
  widzi, i dostają kontekst z tego samego gettera.
- **Ostrzeżenia o przestarzałości pochodzą od Strawberry.** W Litestar 2.24 kontroler ze Strawberry
  0.332 deklaruje własne zależności, `context`, `root_value`, `response`, wyłącznie po nazwie, a Litestar
  ostrzega o każdej z nich — dlatego test jest uruchamiany z `-W ignore::DeprecationWarning`. Argument
  `users` funkcji `get_context` nie wywołuje żadnego ostrzeżenia.
- W odróżnieniu od FastAPI nic więcej nie jest potrzebne: Litestar przekazuje zależności kontrolera
  do `ClientPlugin` bez zmian; różnicę opisuje [FastAPI](fastapi.md#strawberry-graphql).
