# <a id="litestar"></a>Litestar

[English](../../guide/litestar.md) · **Русский** · [简体中文](../zh-CN/litestar.md) · [Español](../es/litestar.md) · [Português (Brasil)](../pt-BR/litestar.md) · [日本語](../ja/litestar.md) · [Polski](../pl/litestar.md)

← [Документация](../README.ru.md#documentation)

Обработчик маршрута в Litestar тоже получает клиент по аннотации типа — через плагин:

```bash
pip install "nuke-di[litestar]"
```

Нужен Litestar 2.15 или новее. С клиентами из примеров для [FastAPI](fastapi.md):

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

`ClientPlugin()` нашёл `users: UserService` в `get_user` и `db: Database` в зависимости
`current_user`, передал оба клиента в Litestar как зависимости и подключил их при старте.

Правила:

- **Где заполняются клиенты.** В аргументах HTTP-обработчиков и обработчиков `@websocket`, с которыми
  создаётся приложение, включая обработчики роутеров и контроллеров на любой глубине, а также всех
  зависимостей, объявленных на приложении, роутере, контроллере или обработчике: функций и классов.
- **По имени.** Litestar передаёт зависимости по имени аргумента, поэтому nuke-di регистрирует каждый
  аргумент-клиент под его именем на уровне приложения. Одно имя — один клиент во всём приложении:
  `users: UserService` в одном обработчике и `users: Billing` в другом выбрасывают `TypeError` при создании
  приложения. Зависимость с тем же именем, объявленная приложением, роутером, контроллером или
  обработчиком, имеет приоритет над клиентом.
- **Экземпляры.** `Client` — это один экземпляр на контейнер, а `NotSingletonClient` — один экземпляр
  на имя аргумента.
- **Lifespan.** Клиенты подключаются до собственных `lifespan=` и `on_startup=` приложения и отключаются
  после его хуков `on_shutdown=`, которые Litestar вызывает последними. `Shutdown` и `BackgroundTasks`
  ведут себя так же, как в [FastAPI](fastapi.md).
- **Функция остаётся функцией.** Теперь её аргументы-клиенты аннотированы как явные зависимости
  Litestar, значение которых не валидируется, — `Annotated[UserService, Dependency(), SkipValidationMarker()]`:
  именно этого Litestar 2.23 требует вместо зависимости, сопоставленной только по имени. Прямой вызов
  функции работает как раньше.
- **Плагины.** Ставьте `ClientPlugin()` после всех плагинов, которые добавляют обработчики маршрутов:
  он видит те обработчики, которые есть у приложения к моменту, когда до него доходит очередь.
- **Другой контейнер.** `ClientPlugin(container)`.

**Тестирование.** Как и с FastAPI, тест подменяет клиент до того, как `TestClient` запустит приложение:

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

**Что не поддерживается.** WebSocket-слушатель, `@websocket_listener` или класс `WebsocketListener`,
клиенты не принимает: Litestar читает его сигнатуру в момент объявления, ещё до того, как её увидит
плагин, поэтому приложение выбрасывает `TypeError` и предлагает вместо него обработчик `@websocket`.
Аргумент-клиент с именем, которое Litestar резервирует за собой, например `state` или `request`, тоже
приводит к `TypeError`. Обработчик, зарегистрированный после создания приложения через `app.register()`,
не виден.

## <a id="differences-from-fastapi"></a>Отличия от FastAPI

Обработчик в обоих случаях пишется одинаково: `users: UserService`. Различия идут от того, как каждый
фреймворк внедряет зависимости: FastAPI читает `Depends` в сигнатуре каждой функции, а Litestar
сопоставляет зависимость по имени аргумента, поэтому `ClientPlugin` предоставляет каждый клиент под
именем его аргумента на уровне приложения ([ADR-0004](../../adr/0004-litestar-clients-by-name.md)).

| | FastAPI | Litestar |
|---|---|---|
| Подключение | `setup(app)` до маршрутов; роутеры через `ClientRouter` | `ClientPlugin()` в `plugins=`; любой роутер или контроллер |
| Какие обработчики видны | Каждый маршрут, объявленный после `setup(app)`, на приложении или на включённом `ClientRouter` | Обработчики, с которыми создано приложение; добавленный позже через `app.register()` — нет |
| Имена аргументов | Свободные: `users: UserService` здесь и `users: Billing` там | Одно имя — один клиент во всём приложении; два клиента под одним именем выбрасывают `TypeError` при создании приложения |
| `NotSingletonClient` | Один экземпляр на аргумент | Один экземпляр на имя аргумента, общий для всех обработчиков, которые используют это имя |
| Что меняется в функции | Её `__signature__`; `get_type_hints()` по-прежнему показывает `UserService` | Её `__annotations__`; `get_type_hints(include_extras=True)` показывает `Annotated[UserService, Dependency(), SkipValidationMarker()]` |
| WebSocket | Эндпоинты `@app.websocket` | Обработчики `@websocket`; WebSocket-слушатель выбрасывает `TypeError` |
| Собственный lifespan приложения | Выполняется внутри: клиенты подключаются до него и отключаются после | Клиенты подключаются до `lifespan=` и `on_startup=`, отключаются после `on_shutdown=` |
| Подмена зависимости в тесте | `override()` или `app.dependency_overrides` | `override()` или зависимость с тем же именем на каком-либо уровне, которая имеет приоритет над клиентом |
| Другой контейнер | `setup(app, container)` и `ClientRouter(container=container)` | `ClientPlugin(container)` |

## <a id="litestar-3"></a>Litestar 3

Litestar 3 ещё не вышел: на 2026-10-10 последний релиз на PyPI — 2.24.0 от 2026-06-11, и
пре-релиза 3.0 нет. Про него известны две вещи, и nuke-di ничего не меняет, пока он не выйдет:

- **Выводимые зависимости уходят.** Litestar 2.24 предупреждает о зависимости, сопоставленной только по
  имени: `Inferred dependencies will stop working in Litestar 3.0`. Аннотация, которую пишет nuke-di,
  `Annotated[UserService, Dependency(), SkipValidationMarker()]`, — это то, что означают
  `NamedDependency[...]` и `SkipValidation[...]`, то есть явная форма, и ни о чём из неё Litestar 2.24
  не предупреждает.
- **Запланировано внедрение по типу.** [Анонс v3](https://litestar.dev/blog/v3-announcement) от
  2026-07-26 планирует `TypeDependency[SomeService]` рядом с `NamedDependency`, с провайдером, ключом
  которого служит тип, `dependencies={SomeService: provide_some_service}`, и называет эту переработку DI
  той функцией, которая всё ещё отделяет 3.0 от беты.

С провайдерами, ключом которых служит тип, `ClientPlugin` мог бы предоставлять каждый клиент под его
классом, а не под именем аргумента, и строки таблицы выше про имена и экземпляры исчезли бы. Будет ли
так, решится, когда выйдут 3.0 и его API, пересмотром ADR-0004. Обработчики не меняются в любом случае:
они объявляют `users: UserService`, и только плагин решает, как сообщить об этом Litestar.

## <a id="strawberry-graphql"></a>Strawberry GraphQL

Контроллер Strawberry для Litestar строит контекст GraphQL зависимостью Litestar, поэтому функция
получения контекста принимает клиенты, как любая другая зависимость, а резолверы читают их из
`info.context`:

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

Правила:

- **Функция получения контекста — это функция.** `BaseContext` Strawberry для Litestar — это `Struct`
  из msgspec; переданный как сам `context_getter=Context`, он валит каждый запрос с
  `msgspec.ValidationError`.
- **Подписки** работают на websocket-обработчике того же контроллера, который `ClientPlugin` тоже видит,
  и получают контекст из той же функции.
- **Предупреждения об устаревании — от Strawberry.** На Litestar 2.24 контроллер Strawberry 0.332
  объявляет собственные зависимости `context`, `root_value`, `response` только по имени, и Litestar
  предупреждает о каждой, поэтому тест запускается с `-W ignore::DeprecationWarning`. Аргумент `users`
  функции `get_context` предупреждений не вызывает.
- В отличие от FastAPI, больше ничего не нужно: Litestar передаёт зависимости контроллера в
  `ClientPlugin` как есть; в чём разница, см. [FastAPI](fastapi.md#strawberry-graphql).
