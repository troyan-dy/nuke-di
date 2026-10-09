# <a id="testing"></a>Тестирование

[English](../../guide/testing.md) · **Русский** · [简体中文](../zh-CN/testing.md) · [Español](../es/testing.md) · [Português (Brasil)](../pt-BR/testing.md) · [日本語](../ja/testing.md) · [Polski](../pl/testing.md)

← [Документация](../README.ru.md#documentation)

**Клиент через контейнер.** Зарегистрируйте моки до разрешения дерева; тогда каждый потребитель
получит мок:

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

**Клиент на один блок, через `override()`.** `override(cls, new=None)` регистрирует подмену,
как `mock()`, но она действует до конца блока `with`, даже на протяжении нескольких циклов `async with`,
а при выходе контейнер очищается, так что ничего из разрешённого с подменой не утекает в следующий тест.
Работает и с глобальным `DI`:

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

Асинхронные тесты в этом разделе используют [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) с
`asyncio_mode = auto` в `pytest.ini`; без него pytest не запускает тесты, объявленные через `async def`.

```console
$ pytest -q -s test_greet.py
Hello, alice!
after the block: OrderedDict()
.Hello, bob!
.
2 passed in 0.01s
```

`database: connected` не выводится ни разу: подмена не подключается.

Правила:

- **Подменяйте до разрешения.** Подмена, зарегистрированная после разрешения `cls`, дошла бы
  только до потребителей, разрешённых позже, а ранние сохранили бы настоящий клиент, поэтому `mock()`
  вместо этого выбрасывает исключение:

  ```python
  DI.inject(handler)  # resolves UserService -> Database
  DI.mock(Database)  # ConnectError: Database is already resolved, call mock() before resolve() or inject()
  ```

- **`override()` начинает с контейнера без разрешённых клиентов.** Иначе очистка при выходе
  молча выбросила бы то, что было разрешено до блока, поэтому он выбрасывает
  `ConnectError: override(Database) needs a container without resolved clients, found: Database, UserService`.
  Сначала вызовите `DI.flush()` или используйте фикстуру `global_di`, описанную ниже.
- **Одна подмена на класс.** Повторный вызов `mock(cls)` возвращает уже зарегистрированную подмену;
  `mock(cls, other)` и `override(cls)` выбрасывают `ConnectError: Database already has a
  replacement`.
- **Подмены не подключаются.** Их `connect()` / `disconnect()` никогда не вызываются, и они
  не участвуют в [слоях](clients.md#layers).
- **Сколько живёт подмена.** Подмену из `mock()` сбрасывает следующий `flush()`, включая тот, что
  выполняется в конце `disconnect()`: тесту, который подключает контейнер больше одного раза, стоит
  использовать `override()` — его подмена переживает любой `flush()` до конца своего блока. Исключение
  внутри блока пробрасывается без изменений; обычный выход из блока, пока контейнер ещё подключён,
  выбрасывает `ConnectError`.
- **Вложенность.** Блоки для разных классов можно вкладывать друг в друга, если каждый открывается
  до того, как что-либо разрешено, например `with DI.override(Database), DI.override(Clock):`; выход
  из внутреннего блока сохраняет подмену внешнего.

**Фикстуры pytest.** Установка `nuke-di` регистрирует плагин pytest с двумя фикстурами. Ни одна из
них не autouse, поэтому существующие тесты работают ровно как раньше:

| Фикстура    | Что даёт                                               |
|-------------|--------------------------------------------------------|
| `di`        | Новый `Dependencies` на один тест                      |
| `global_di` | Глобальный `DI`, очищенный до и после теста            |

Тест, который оставил контейнер подключённым, получает ошибку на этапе teardown, а контейнер всё равно
очищается, так что следующий тест начинается с чистого листа:

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

Фикстуры не могут сами отключить забытый контейнер: к моменту teardown event loop теста может быть
уже закрыт. `global_di` защищает только те тесты, которые её запрашивают: тест, использующий глобальный
`DI` без неё, по-прежнему может оставить клиенты следующему. Проект, в котором определена собственная
фикстура `di`, сохраняет её, так как фикстура из `conftest.py` важнее фикстуры плагина;
`pytest -p no:nuke_di` отключает плагин.

**Джоба напрямую.** Импорт модуля не запускает джобу, поэтому вызовите функцию с
моками и параметрами:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    pg.upsert.assert_awaited_once_with("users", ["row"])
```

**Джоба через контейнер**, с клиентами, связанными так же, как в продакшене:

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

**Каждый entrypoint разрешается.** Импорт модуля не запускает его джобу или воркер, а `inject()`
строит дерево, ничего не подключая, поэтому один тест проверяет проводку всех entrypoint-ов в CI:
цикл, аргумент без аннотации, обязательный аргумент не-клиент или `__init__`, который бросает
исключение, валят его с той же ошибкой, что напечатал бы реальный запуск, и база данных не нужна:

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

Сохраните контейнер, чтобы получить [граф](clients.md#the-graph) entrypoint-а для его README:
`deps = Dependencies(); deps.inject(sync.sync); print(deps.graph().to_mermaid())`.

**Воркер.** `Shutdown.set()` делает то же, что сделал бы SIGTERM:

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
