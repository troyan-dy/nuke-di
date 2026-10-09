# <a id="the-container"></a>Контейнер

[English](../../guide/container.md) · **Русский** · [简体中文](../zh-CN/container.md) · [Español](../es/container.md) · [Português (Brasil)](../pt-BR/container.md) · [日本語](../ja/container.md) · [Polski](../pl/container.md)

← [Документация](../README.ru.md#documentation)

`Dependencies` — это контейнер. `DI` — готовый глобальный экземпляр; создавайте собственный,
когда нужна изоляция, например в тестах.

| Метод                | Описание                                                                |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Построить `cls` и его дерево зависимостей. Идемпотентен для `Client`.   |
| `inject(func)`       | Вернуть `functools.partial(func, ...)` с привязанными аргументами-клиентами. У каждого аргумента `func`, кроме `*args` / `**kwargs`, должна быть аннотация типа. |
| `connect()`          | Вызвать `connect()` у каждого разрешённого клиента после его зависимостей. |
| `disconnect()`       | Вызвать `disconnect()` у каждого клиента после его потребителей, затем очистить контейнер через `flush()`. |
| `async with`         | `connect()` при входе, `disconnect()` при выходе.                       |
| `mock(cls, new=None)`| Зарегистрировать подмену для `cls` (по умолчанию — autospec-мок) до следующего `flush()`. Вызывается до разрешения `cls`. |
| `override(cls, new=None)` | Подмена на время блока `with`, затем `flush()`; см. [Тестирование](testing.md). |
| `flush()`            | Забыть все разрешённые клиенты.                                         |
| `timings`            | По одному `ClientTiming` на клиент последнего `connect()`; см. [Время старта](clients.md#startup-timings). |
| `graph()`            | `Graph` разрешённых клиентов с их зависимостями, включая `to_mermaid()`; см. [Граф](clients.md#the-graph). |

Результат `inject()` сохраняет тип возвращаемого значения функции, а его оставшиеся аргументы
не типизированы: тайпчекер не умеет вычитать аргументы-клиенты из сигнатуры.

`resolve`, `inject`, `mock`, `override` и `flush` работают, только пока контейнер отключён:
всё дерево строится до старта.

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: resolve(Cache): the container is already connected; ...
```

Контейнер безопасно использовать из нескольких потоков: один замок на контейнер сериализует `resolve`,
`inject`, `mock`, `override` и `flush`, поэтому синглтон, запрошенный двумя потоками одновременно, создаётся
один раз. `connect()` и `disconnect()` принадлежат одному циклу событий.

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
