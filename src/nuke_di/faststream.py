"""
FastStream integration: subscriber handlers and their dependencies take clients by type hint.

See docs/specs/faststream.md and docs/adr/0003-fastapi-signature-rewrite.md.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from faststream import Depends, FastStream
from faststream.asgi import AsgiFastStream

from nuke_di._integration import Binding, DependsFramework, bind, connected, unique
from nuke_di.core import DI, Dependencies

__all__ = ("setup",)


def _noop() -> None: ...  # pragma: no cover


_FASTSTREAM = DependsFramework(
    # FastStream takes `Depends` from fast-depends, whose marker class moved between its versions
    depends=type(Depends(_noop)),
    make_depends=Depends,
    not_started=(
        "{client} was not started with the app: declare its subscriber on a broker of the app before the app "
        "starts, or on a router included into that broker"
    ),
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`",
)


def setup(app: FastStream | AsgiFastStream, container: Dependencies = DI) -> None:
    """
    Fill client arguments of the subscribers of `app`'s brokers, and run `container` with the app: connect
    it before the brokers start, disconnect it after they stop.
    """
    original = app.lifespan_context
    if getattr(original, "__nuke_di__", False):
        raise TypeError("setup() was already called for this app")

    def rewrite(call: Callable[..., Any]) -> Callable[..., Any]:
        # FastStream builds a subscriber from its declared function on every start, through this decorator.
        # The signature is rewritten on startup already; this covers a broker started without the app, so
        # that its handler raises "not connected" instead of reading the client from the message
        bind(call, container, _FASTSTREAM)
        return call

    for broker in app.brokers:
        config = broker.config.fd_config
        config.call_decorators = (*config.call_decorators, rewrite)

    @asynccontextmanager
    async def lifespan(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        # The app's own lifespan runs inside, so its startup and shutdown code can use the clients; the
        # brokers start after both and stop before them
        async with connected(container, _app_bindings(app, container)), original(*args, **kwargs) as state:
            yield state

    lifespan.__nuke_di__ = True  # type: ignore[attr-defined]
    app.lifespan_context = lifespan


def _app_bindings(app: FastStream | AsgiFastStream, container: Dependencies) -> list[Binding]:
    """
    The clients of every subscriber the brokers of `app` serve, routers included, each once.
    """
    bindings: list[Binding] = []
    for broker in app.brokers:
        for subscriber in broker.subscribers:
            # The dependencies of the broker and of the routers on the way
            outer = getattr(subscriber, "_outer_config", None)
            for depends in getattr(outer, "broker_dependencies", ()):
                bindings += bind(depends.dependency, container, _FASTSTREAM)
            for item in subscriber.calls:
                bindings += bind(_declared(item.handler), container, _FASTSTREAM)
                for depends in item.dependencies:
                    bindings += bind(depends.dependency, container, _FASTSTREAM)
    return unique(bindings)


def _declared(handler: Any) -> Callable[..., Any]:
    """
    The function a subscriber was declared with.
    """
    # FastStream 0.7 keeps it apart from the function composed on start; 0.6 keeps only `_original_call`,
    # which is the declared function for an `async def`
    declared = getattr(handler, "_declared_call", None)
    return declared if declared is not None else handler._original_call  # type: ignore[no-any-return]
