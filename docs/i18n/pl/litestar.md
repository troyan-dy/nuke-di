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
