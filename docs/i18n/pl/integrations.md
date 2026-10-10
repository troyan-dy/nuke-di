# <a id="writing-an-integration"></a>Pisanie integracji

[English](../../guide/integrations.md) · [Русский](../ru/integrations.md) · [简体中文](../zh-CN/integrations.md) · [Español](../es/integrations.md) · [Português (Brasil)](../pt-BR/integrations.md) · [日本語](../ja/integrations.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

Integracja z frameworkiem robi dwie rzeczy: handlery frameworka przyjmują klientów po adnotacji typu, przez
własny mechanizm wstrzykiwania zależności frameworka, a kontener łączy się, gdy aplikacja startuje, i rozłącza,
gdy się zatrzymuje. Integracje z [FastAPI](fastapi.md), [Litestar](litestar.md), [FastStream](faststream.md),
[MCP](mcp.md), [aiogram](aiogram.md) i [taskiq](taskiq.md) są zbudowane na `nuke_di.integration`, a integracja
z innym frameworkiem nie potrzebuje od nuke-di niczego poza nim i publicznym API.
Framework bez własnego wstrzykiwania zależności, taki jak Starlette czy Quart, nie potrzebuje integracji:
[`nuke_di.asgi.lifespan()`](asgi.md), zbudowany na tym samym zestawie, łączy klientów, a handlery
pobierają ich od niego.
Serwer bez własnego wstrzykiwania zależności, taki jak grpc.aio, aiohttp, websockets, APScheduler, Textual czy
worker Temporal, nie potrzebuje żadnej integracji: działa wewnątrz `@worker`, zobacz
[Serwery wewnątrz workera](servers-in-workers.md).

| Nazwa | Co robi |
|---|---|
| `Framework(name, not_started, not_connected)` | Co słyszą użytkownicy frameworka, gdy brakuje klienta; `{client}` w komunikacie to nazwa klasy klienta. |
| `DependsFramework(..., depends, make_depends, per_container=True)` | Framework, który wstrzykuje przez znaczniki `Depends(...)`: `depends` to klasa jego znaczników, `make_depends` tworzy znacznik dla funkcji. |
| `bind(call, container, framework)` | Przepisuje sygnaturę handlera, funkcji-zależności lub klasy-zależności oraz zależności, których używa: każdy argument-klient staje się `Annotated[Client, Depends(...)]`. Zwraca `Binding` każdego klienta. Adnotacja typu, której nie da się obliczyć, na przykład nazwa zaimportowana pod `TYPE_CHECKING`, zostaje w zapisanej postaci, a pozostałe argumenty i tak są przepisywane. |
| `Binding(cls, container, framework)` | Jeden argument-klient: `cls` to klasa klienta, `instance` to klient rozwiązany przez `running()` albo `None` przed startem i po zamknięciu. `get()` go zwraca albo zgłasza `not_started` / `not_connected`. Tworzy je `bind()`; integracja bez `Depends` sama tworzy po jednym na każdy argument-klient. |
| `running(container, bindings)` | Asynchroniczny menedżer kontekstu: rozwiązuje klientów z `bindings`, łączy kontener, a przy wyjściu ustawia `Shutdown`, zatrzymuje `BackgroundTasks` i rozłącza. `ConnectError` lub `InitializeDependencyError` staje się `RuntimeError`, który serwer zgłasza jako nieudany start; błąd drzewa klientów, np. cykl, przechodzi bez zmian. |
| `wrap_lifespan(original, container, bindings)` | Lifespan, który uruchamia własny lifespan aplikacji `original` wewnątrz `running()`; `bindings` jest wywoływane przy starcie, więc handlery zadeklarowane po `setup()` zostaną znalezione. |
| `client_of(hint, *markers)` | Klient, o którego prosi adnotacja typu, albo `None`: `Client` lub `Annotated[Client, ...]` bez żadnego z `markers`. |
| `unique(bindings)` | `bindings` bez powtórzeń: do jednej zależności często prowadzi kilka handlerów. |

## <a id="a-framework-with-depends"></a>Framework z `Depends`

FastAPI i FastStream (przez fast-depends) odczytują `inspect.signature()` handlera i wywołują zależność
z każdego znacznika `Depends(...)`, tak samo jak każdy framework, którego znaczniki działają w ten sam
sposób. Dla nich `bind()` zastępuje `users: UserService` przez
`users: Annotated[UserService, Depends(binding.get)]`, a resztę robi framework. Integracja z FastStream,
napisana wyłącznie na publicznym zestawie, to ten moduł:

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

Trzy rzeczy są specyficzne dla frameworka i każda integracja znajduje je we wnętrzu swojego frameworka:

- **Kiedy odczytywana jest sygnatura.** `bind()` musi się wykonać, zanim framework odczyta sygnaturę handlera.
  FastAPI odczytuje ją przy deklaracji trasy, więc `nuke_di.fastapi` wiąże w swojej klasie trasy; FastStream
  odczytuje ją, gdy tworzy subscriber, przy każdym starcie, więc powyższy moduł wiąże w hooku `call_decorators`.
- **Gdzie są handlery.** Przy starcie `wrap_lifespan()` wywołuje `bindings()`, aby dowiedzieć się, których
  klientów rozwiązać: integracja przechodzi po trasach, subscriberach lub zadaniach aplikacji, wiąże każde
  z nich i zwraca `Binding`i. Klient, do którego nic w aplikacji nie prowadzi, nie jest łączony.
- **Gdzie jest lifespan.** `wrap_lifespan()` zastępuje lifespan aplikacji; własny lifespan aplikacji działa
  wewnątrz niego, więc jego kod startowy może korzystać z klientów.

Prawdziwy `nuke_di.faststream` dodaje to, co przykład pomija: FastStream 0.6, zależności brokerów
i routerów oraz jeden hook przepisywania na broker, gdy współdzieli go kilka aplikacji.

### <a id="per-container"></a>`per_container`

`bind()` przepisuje funkcję w miejscu i zapamiętuje kontener, z którym została związana. Przy `per_container=True`
(domyślnie) funkcja związana z jednym kontenerem i zadeklarowana ponownie dla innego jest wiązana ponownie, więc
każda aplikacja zachowuje powiązania, które przechwyciła. Działa to tylko z frameworkiem, który odczytuje sygnaturę
handlera raz, przy jego deklaracji, i nigdy więcej: sygnatura przepisana dla drugiego kontenera trafiłaby do
pierwszej aplikacji przy następnym odczycie przez framework. Żadna integracja dostarczana z nuke-di już tego nie
używa: FastAPI odczytywał sygnatury w ten sposób do wersji 0.136.

Przy `per_container=False` (FastStream, taskiq, FastAPI) funkcja jest wiązana raz, niezależnie od kontenera, a każda
aplikacja, która startuje, rozwiązuje te same `Binding`i. FastStream tworzy subscriber przy każdym starcie, a z
testowym brokerem nawet zanim wykona się lifespan aplikacji; FastAPI 0.137 i nowsze budują trasy dołączonego
routera przy pierwszym żądaniu do aplikacji. W obu przypadkach sygnatura nie może się zmieniać między aplikacjami.
Koszt: dwie aplikacje, które współdzielą funkcję-handler, działają jedna po drugiej, a druga zgłasza
`RuntimeError: ... is filled for another app that is running`. Wybierz `False`, gdy framework może ponownie
odczytać sygnaturę po starcie pierwszej aplikacji. `nuke_di.fastapi` unika tego kosztu, bo zależność FastAPI może
przyjąć żądanie: każda aplikacja rozwiązuje własną kopię każdego `Binding` we własnym kontenerze, a żądanie wybiera
kopię swojej aplikacji, więc dwie aplikacje na dwóch kontenerach jednocześnie obsługują tę samą funkcję.

## <a id="a-framework-without-depends"></a>Framework bez `Depends`

Litestar dostarcza zależności po nazwie, a aiogram przekazuje je po nazwie z middleware. Tam `bind()` nie ma
zastosowania: użyj `Framework` dla komunikatów, `client_of()`, aby znaleźć argumenty-klientów handlera,
jednego `Binding` na klienta, którego `get` framework wywołuje własnymi środkami, oraz `running()` wewnątrz
lifespan aplikacji. `nuke_di.litestar` to gotowy przykład: rejestruje `Provide(binding.get)` pod nazwą
argumentu. `nuke_di.aiogram` to kolejny: wewnętrzny middleware dispatchera wkłada `binding.instance` do danych
update'u pod nazwą każdego argumentu-klienta handlera, który do niego pasował, a `running()` obejmuje start
i zatrzymanie dispatchera.

## <a id="checking-an-integration"></a>Sprawdzanie integracji

`nuke_di.integration.testing.check()` uruchamia przeciwko twojej integracji kontrakt, którego przestrzega każda
integracja:

- handler przyjmuje klienta po adnotacji typu, połączonego przez cały czas działania aplikacji;
- zależność handlera przyjmuje klienta po adnotacji typu (tylko z `DependsFramework`);
- `override()` przed startem podmienia klienta, którego handler dostaje przez innego klienta;
- handler wywołany bez lifespan aplikacji zgłasza błąd `not_connected` frameworka;
- nieudany `connect()` przerywa start aplikacji błędem `RuntimeError` i zostawia kontener wyczyszczony.

Ma własnych klientów i handlery. Podajesz mu framework i dwie funkcje: `make_app(container,
handler)` zwraca nową aplikację skonfigurowaną z `container`, która obsługuje `handler` — `async def` bez
argumentów poza klientami; `run(app, lifespan)` to asynchroniczny menedżer kontekstu, który uruchamia aplikację,
z jej lifespan tylko wtedy, gdy `lifespan` jest prawdą, i zwraca `send()`, które raz wywołuje handler przez
framework i zgłasza to, co zgłosił handler. Dla powyższego modułu:

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

`check()` to korutyna: uruchom ją pod pytest-asyncio lub anyio. Zgłasza `ExceptionGroup` ze wszystkimi
przypadkami, które się nie powiodły, każdy z notatką z nazwą przypadku. Bez linii z `wrap_lifespan()`
w `setup()` kontener nigdy się nie łączy:

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

(Tracebacki skrócone.) Framework, który zgłasza błąd handlera w odpowiedzi zamiast go rzucić, potrzebuje
`send()`, które go zgłosi: klient testowy frameworka HTTP zwraca 500, więc `send()` sprawdza status. Litestar
umieszcza błąd w odpowiedzi tylko z `debug=True`.
