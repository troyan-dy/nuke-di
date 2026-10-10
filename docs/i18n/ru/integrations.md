# <a id="writing-an-integration"></a>Своя интеграция

[English](../../guide/integrations.md) · **Русский** · [简体中文](../zh-CN/integrations.md) · [Español](../es/integrations.md) · [Português (Brasil)](../pt-BR/integrations.md) · [日本語](../ja/integrations.md) · [Polski](../pl/integrations.md)

← [Документация](../README.ru.md#documentation)

Интеграция с фреймворком делает две вещи: обработчики фреймворка получают клиенты по аннотации типа через
собственное внедрение зависимостей фреймворка, а контейнер подключается при старте приложения и отключается
при его остановке. Интеграции с [FastAPI](fastapi.md), [Litestar](litestar.md), [FastStream](faststream.md)
и [MCP](mcp.md) построены на `nuke_di.integration`, и интеграции с другим фреймворком не нужно от nuke-di
ничего, кроме него и публичного API.

| Имя | Что делает |
|---|---|
| `Framework(name, not_started, not_connected)` | Что сообщают пользователям фреймворка, когда клиента нет; `{client}` в сообщении — имя класса клиента. |
| `DependsFramework(..., depends, make_depends, per_container=True)` | Фреймворк, который внедряет зависимости через маркеры `Depends(...)`: `depends` — класс его маркеров, `make_depends` создаёт маркер для функции. |
| `bind(call, container, framework)` | Переписывает сигнатуру обработчика, функции-зависимости или класса-зависимости, а также зависимостей, которые он использует: каждый аргумент-клиент становится `Annotated[Client, Depends(...)]`. Возвращает `Binding` каждого клиента. |
| `Binding` | Один аргумент-клиент; `get()` возвращает клиент, разрешённый при старте, или выбрасывает `not_started` / `not_connected`. |
| `running(container, bindings)` | Асинхронный контекстный менеджер: разрешает клиенты из `bindings`, подключает контейнер, а на выходе выставляет `Shutdown`, останавливает `BackgroundTasks` и отключает контейнер. `ConnectError` или `InitializeDependencyError` превращается в `RuntimeError`, который сервер показывает как неудачный старт; ошибка дерева клиентов, например цикл, проходит как есть. |
| `wrap_lifespan(original, container, bindings)` | Lifespan, который выполняет собственный lifespan приложения `original` внутри `running()`; `bindings` вызывается при старте, поэтому находятся и обработчики, объявленные после `setup()`. |
| `client_of(hint, *markers)` | Клиент, которого просит аннотация типа, или `None`: `Client` или `Annotated[Client, ...]` без каких-либо из `markers`. |
| `unique(bindings)` | `bindings` без повторов: одна зависимость часто достижима из нескольких обработчиков. |

## <a id="a-framework-with-depends"></a>Фреймворк с `Depends`

FastAPI и FastStream (через fast-depends) читают `inspect.signature()` обработчика и вызывают зависимость
из каждого маркера `Depends(...)`, как и любой фреймворк, чьи маркеры работают так же. Для них `bind()` заменяет
`users: UserService` на `users: Annotated[UserService, Depends(binding.get)]`, а остальное делает фреймворк.
Интеграция с FastStream, написанная только на публичном наборе, — вот этот модуль:

```python
# myapp/faststream_di.py
from typing import Any

from faststream import Depends, FastStream

from nuke_di import DI, Dependencies
from nuke_di.integration import Binding, DependsFramework, bind, unique, wrap_lifespan


def _noop() -> None: ...


FRAMEWORK = DependsFramework(
    name="FastStream",
    # The class of FastStream's markers, and the function that builds one
    depends=type(Depends(_noop)),
    make_depends=Depends,
    not_started="{client} was not started with the app: declare its subscriber before the app starts",
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`",
    # FastStream builds a subscriber on every start: a function is bound once, whatever the container
    per_container=False,
)


def setup(app: FastStream, container: Dependencies = DI) -> None:
    # Connect the container around the app's own lifespan, with the clients found on startup
    app.lifespan_context = wrap_lifespan(app.lifespan_context, container, lambda: _bindings(app, container))
    for broker in app.brokers:
        # Rewrite a subscriber's function whenever FastStream builds the subscriber
        config = broker.config.fd_config
        config.call_decorators = (*config.call_decorators, _Rewrite(container))


class _Rewrite:
    def __init__(self, container: Dependencies) -> None:
        self.container = container

    def __call__(self, call: Any) -> Any:
        bind(call, self.container, FRAMEWORK)
        return call


def _bindings(app: FastStream, container: Dependencies) -> list[Binding]:
    # The clients of every subscriber and of the dependencies it declares, each once
    bindings: list[Binding] = []
    for broker in app.brokers:
        for subscriber in broker.subscribers:
            for item in subscriber.calls:
                bindings += bind(item.handler._declared_call, container, FRAMEWORK)
                for depends in item.dependencies:
                    bindings += bind(depends.dependency, container, FRAMEWORK)
    return unique(bindings)
```

Три вещи зависят от фреймворка, и каждая интеграция находит их во внутренностях своего фреймворка:

- **Когда читается сигнатура.** `bind()` должен выполниться до того, как фреймворк прочитает сигнатуру
  обработчика. FastAPI читает её при объявлении маршрута, поэтому `nuke_di.fastapi` вызывает `bind()` в своём
  классе маршрута; FastStream читает её, когда создаёт подписчика, при каждом старте, поэтому модуль выше
  вызывает `bind()` в хуке `call_decorators`.
- **Где обработчики.** При старте `wrap_lifespan()` вызывает `bindings()`, чтобы узнать, какие клиенты
  разрешить: интеграция обходит маршруты, подписчиков или задачи приложения, вызывает `bind()` для каждого
  и возвращает `Binding`. Клиент, до которого никто в приложении не дотягивается, не подключается.
- **Где lifespan.** `wrap_lifespan()` подменяет lifespan приложения; собственный lifespan приложения
  выполняется внутри него, поэтому его код старта может пользоваться клиентами.

Настоящий `nuke_di.faststream` добавляет то, что пример опускает: FastStream 0.6, зависимости брокеров
и роутеров и по одному хуку переписывания на брокер, когда его разделяют несколько приложений.

### <a id="per-container"></a>`per_container`

`bind()` переписывает функцию на месте и запоминает контейнер, к которому она привязана. С `per_container=True`
(по умолчанию, FastAPI) функция, привязанная к одному контейнеру и объявленная снова для другого, привязывается
заново: FastAPI читает сигнатуру маршрута один раз, при его объявлении, поэтому каждое приложение сохраняет
привязки, которые захватило, и два приложения на двух контейнерах могут обслуживать одну функцию одновременно.

С `per_container=False` (FastStream) функция привязывается один раз, каким бы ни был контейнер, и каждое
стартующее приложение разрешает одни и те же `Binding`. FastStream создаёт подписчика при каждом старте, а под
тестовым брокером — даже до того, как запустится lifespan приложения, поэтому сигнатура не должна меняться от
одного приложения к другому. Цена: два приложения с общей функцией-обработчиком работают по очереди, а второе
выбрасывает `RuntimeError: ... is filled for
another app that is running`. Выбирайте `False`, когда фреймворк может снова прочитать сигнатуру после того,
как первое приложение уже стартовало.

## <a id="a-framework-without-depends"></a>Фреймворк без `Depends`

Litestar предоставляет зависимости по имени, а aiogram передаёт их по имени из middleware. Там `bind()`
не подходит: используйте `Framework` для сообщений, `client_of()`, чтобы найти аргументы-клиенты обработчика,
по одному `Binding` на клиент, чей `get` фреймворк вызывает своими средствами, и `running()` внутри lifespan
приложения. Готовый пример — `nuke_di.litestar`: он регистрирует `Provide(binding.get)` под именем аргумента.

## <a id="checking-an-integration"></a>Проверка интеграции

`nuke_di.integration.testing.check()` прогоняет против вашей интеграции контракт, который соблюдает каждая
интеграция:

- обработчик получает клиент по аннотации типа, подключённый на всё время работы приложения;
- зависимость обработчика получает клиент по аннотации типа (только с `DependsFramework`);
- `override()` до старта подменяет клиент, который обработчик получает через другой клиент;
- обработчик, вызванный без lifespan приложения, выбрасывает ошибку `not_connected` фреймворка;
- неудачный `connect()` срывает старт приложения с `RuntimeError` и оставляет контейнер сброшенным.

Клиенты и обработчики у неё свои. Вы передаёте фреймворк и две функции: `make_app(container,
handler)` возвращает новое приложение, настроенное с `container` и обслуживающее `handler` — `async def`
без аргументов, кроме клиентов; `run(app, lifespan)` — асинхронный контекстный менеджер, который запускает
приложение, с его lifespan только когда `lifespan` истинно, и отдаёт `send()`, который один раз вызывает
обработчик через фреймворк и выбрасывает то, что выбросил обработчик. Для модуля выше:

```python
# tests/test_contract.py
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from faststream import FastStream, TestApp
from faststream.nats import NatsBroker, TestNatsBroker

from myapp.faststream_di import FRAMEWORK, setup
from nuke_di import Dependencies
from nuke_di.integration.testing import Send, check


def make_app(container: Dependencies, handler: Callable[..., Any]) -> FastStream:
    broker = NatsBroker()
    app = FastStream(broker)
    setup(app, container)
    broker.subscriber("check")(handler)
    return app


@asynccontextmanager
async def run(app: FastStream, lifespan: bool) -> AsyncIterator[Send]:
    # connect_only: FastStream would guess it from the mention of TestApp below
    async with TestNatsBroker(app.broker, connect_only=lifespan) as broker:
        if lifespan:
            async with TestApp(app):
                yield lambda: broker.publish(None, "check")
        else:
            yield lambda: broker.publish(None, "check")


async def test_contract() -> None:
    await check(FRAMEWORK, make_app, run)
```

```console
$ pytest -q tests/test_contract.py
.                                                                        [100%]
1 passed in 0.29s
```

`check()` — корутина: запускайте её под pytest-asyncio или anyio. Она выбрасывает `ExceptionGroup` из всех
провалившихся случаев, у каждого — примечание с именем случая. Если убрать из `setup()` строку с
`wrap_lifespan()`, контейнер так и не подключается:

```console
$ pytest -q --tb=short tests/test_contract.py
F                                                                        [100%]
  | ExceptionGroup: the FastStream integration breaks the nuke-di contract (4 sub-exceptions)
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case handler: a handler takes a client by type hint, connected for the time the app runs
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case dependency: a dependency of a handler takes a client by type hint
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case override: override() before startup replaces a client the handler gets through another client
    | AssertionError: expected a RuntimeError with 'nuke-di clients failed to start' about Broken, got RuntimeError('Broken is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`')
    | FastStream integration, case failed_connect: a failed connect() fails the app's startup with a RuntimeError and leaves the container flushed
FAILED tests/test_contract.py::test_contract - ExceptionGroup: the FastStream...
1 failed in 0.17s
```

(Трейсбеки сокращены.) Фреймворку, который сообщает об ошибке обработчика, а не выбрасывает её, нужен `send()`,
который её выбрасывает: тестовый клиент HTTP-фреймворка возвращает 500, поэтому `send()` проверяет статус.
Litestar кладёт ошибку в ответ только с `debug=True`.
