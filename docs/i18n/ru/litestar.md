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
