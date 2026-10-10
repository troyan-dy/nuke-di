# Writing an integration

**English** · [Русский](../i18n/ru/integrations.md) · [简体中文](../i18n/zh-CN/integrations.md) · [Español](../i18n/es/integrations.md) · [Português (Brasil)](../i18n/pt-BR/integrations.md) · [日本語](../i18n/ja/integrations.md) · [Polski](../i18n/pl/integrations.md)

← [Documentation](../../README.md#documentation)

An integration with a framework does two things: the framework's handlers take clients by type hint, through
the framework's own dependency injection, and the container connects when the app starts and disconnects when
it stops. The [FastAPI](fastapi.md), [Litestar](litestar.md), [FastStream](faststream.md) and [MCP](mcp.md)
integrations are built on `nuke_di.integration`, and an integration with another framework needs nothing from
nuke-di beyond it and the public API.

| Name | What it does |
|---|---|
| `Framework(name, not_started, not_connected)` | What the users of a framework are told when a client is missing; `{client}` in a message is the client's class name. |
| `DependsFramework(..., depends, make_depends, per_container=True)` | A framework that injects through `Depends(...)` markers: `depends` is the class of its markers, `make_depends` builds one for a function. |
| `bind(call, container, framework)` | Rewrites the signature of a handler, a dependency function or a dependency class, and of the dependencies it uses: each client argument becomes `Annotated[Client, Depends(...)]`. Returns the `Binding` of each client. |
| `Binding` | One client argument; `get()` returns the client resolved on startup, or raises `not_started` / `not_connected`. |
| `running(container, bindings)` | An async context manager: resolves the clients of `bindings`, connects the container, and on exit sets `Shutdown`, stops the `BackgroundTasks` and disconnects. A `ConnectError` or `InitializeDependencyError` becomes a `RuntimeError`, which a server reports as a failed startup; an error of the client tree, such as a cycle, passes as it is. |
| `wrap_lifespan(original, container, bindings)` | A lifespan that runs the app's own `original` lifespan inside `running()`; `bindings` is called on startup, so handlers declared after `setup()` are found. |
| `client_of(hint, *markers)` | The client a type hint asks for, or `None`: `Client`, or `Annotated[Client, ...]` without any of `markers`. |
| `unique(bindings)` | `bindings` without repeats: one dependency is often reachable from several handlers. |

## <a id="a-framework-with-depends"></a>A framework with `Depends`

FastAPI and FastStream (through fast-depends) read `inspect.signature()` of a handler and call the dependency
in each `Depends(...)` marker, and so does any framework whose markers work the same way. For them, `bind()` replaces `users: UserService` with
`users: Annotated[UserService, Depends(binding.get)]`, and the framework does the rest. The FastStream
integration, written on the public kit alone, is this module:

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

Three things are specific to a framework, and each integration finds them in that framework's internals:

- **When the signature is read.** `bind()` must run before the framework reads the handler's signature.
  FastAPI reads it when a route is declared, so `nuke_di.fastapi` binds in its route class; FastStream reads it
  when it builds a subscriber, on every start, so the module above binds in a `call_decorators` hook.
- **Where the handlers are.** On startup, `wrap_lifespan()` calls `bindings()` to learn which clients to
  resolve: the integration walks the app's routes, subscribers or tasks, binds each one and returns the
  `Binding`s. A client nobody reaches from the app is not connected.
- **Where the lifespan is.** `wrap_lifespan()` replaces the app's lifespan; the app's own lifespan runs inside
  it, so its startup code can use the clients.

The real `nuke_di.faststream` adds what the example leaves out: FastStream 0.6, the dependencies of brokers
and routers, and one rewrite hook per broker when several apps share it.

### <a id="per-container"></a>`per_container`

`bind()` rewrites a function in place and remembers the container it was bound to. With `per_container=True`
(the default, FastAPI) a function bound to one container and declared again for another is bound again: FastAPI
reads a route's signature once, when the route is declared, so each app keeps the bindings it captured, and two
apps on two containers can serve the same function at once.

With `per_container=False` (FastStream) a function is bound once, whatever the container, and every app that
starts resolves the same `Binding`s. FastStream builds a subscriber on every start, and under a test broker
even before the app's lifespan runs, so the signature must not change from one app to the next. The cost: two
apps that share a handler function run one at a time, and the second raises `RuntimeError: ... is filled for
another app that is running`. Choose `False` when the framework may read a signature again after the first
app has started.

## <a id="a-framework-without-depends"></a>A framework without `Depends`

Litestar provides dependencies by name, and aiogram passes them by name from middleware. There `bind()` does
not apply: use `Framework` for the messages, `client_of()` to find the client arguments of a handler, a
`Binding` per client whose `get` the framework calls by its own means, and `running()` inside the app's
lifespan. `nuke_di.litestar` is the worked example: it registers `Provide(binding.get)` under the argument's
name.

## <a id="checking-an-integration"></a>Checking an integration

`nuke_di.integration.testing.check()` runs the contract every integration keeps, against your integration:

- a handler takes a client by type hint, connected for the time the app runs;
- a dependency of a handler takes a client by type hint (with a `DependsFramework` only);
- `override()` before startup replaces a client the handler gets through another client;
- a handler called without the app's lifespan raises the framework's `not_connected` error;
- a failed `connect()` fails the app's startup with a `RuntimeError` and leaves the container flushed.

It brings its own clients and handlers. You give it the framework and two functions: `make_app(container,
handler)` returns a new app set up with `container` that serves `handler`, an `async def` with no arguments
but clients; `run(app, lifespan)` is an async context manager that runs the app, with its lifespan only when
`lifespan` is true, and yields a `send()` that calls the handler once through the framework and raises what
the handler raised. For the module above:

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

`check()` is a coroutine: run it under pytest-asyncio or anyio. It raises an `ExceptionGroup` of every case
that failed, each with a note naming the case. With the `wrap_lifespan()` line of `setup()` left out, the
container never connects:

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

(Tracebacks shortened.) A framework that reports the error of a handler instead of raising it needs a `send()`
that raises it: an HTTP framework's test client returns a 500, so `send()` checks the status. Litestar puts
the error into the response only with `debug=True`.
