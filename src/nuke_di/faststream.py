"""
FastStream integration: subscriber handlers and their dependencies take clients by type hint.

See docs/specs/faststream.md and docs/adr/0003-fastapi-signature-rewrite.md.
"""

from collections.abc import Callable, Iterator
from typing import Any

from faststream import Depends, FastStream
from faststream.asgi import AsgiFastStream

from nuke_di.core import DI, Dependencies
from nuke_di.integration import Binding, DependsFramework, bind, unique, wrap_lifespan

__all__ = ("setup",)


def _noop() -> None: ...  # pragma: no cover


_FASTSTREAM = DependsFramework(
    name="FastStream",
    # FastStream takes `Depends` from fast-depends, whose marker class moved between its versions
    depends=type(Depends(_noop)),
    make_depends=Depends,
    not_started=(
        "{client} was not started with the app: declare its subscriber on a broker of the app before the app "
        "starts, or on a router included into that broker"
    ),
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`",
    # FastStream builds a subscriber on every start, and FastStream 0.6 under a test broker before the app's
    # lifespan runs: the signature must not change from one app to the next
    per_container=False,
)


def setup(app: FastStream | AsgiFastStream, container: Dependencies = DI) -> None:
    """
    Fill client arguments of the subscribers of `app`'s brokers, and run `container` with the app: connect
    it before the brokers start, disconnect it after they stop.
    """
    # Outside the app's own lifespan, which FastStream enters before the startup hooks and the brokers
    app.lifespan_context = wrap_lifespan(app.lifespan_context, container, lambda: _app_bindings(app, container))
    for broker in app.brokers:
        _rewrite_on_build(broker, container)


class _Rewrite:
    """
    Rewrites a subscriber's function whenever FastStream builds the subscriber, i.e. on every broker start:
    a broker started without the app then raises "not connected" instead of reading the client from the
    message. One per broker: a function is bound once, whatever the container.
    """

    def __init__(self, container: Dependencies) -> None:
        self.container = container

    def __call__(self, call: Callable[..., Any]) -> Callable[..., Any]:
        bind(call, self.container, _FASTSTREAM)
        return call


def _rewrite_on_build(broker: Any, container: Dependencies) -> None:
    config = broker.config.fd_config
    rewrite = next((decorator for decorator in config.call_decorators if isinstance(decorator, _Rewrite)), None)
    if rewrite is None:
        config.call_decorators = (*config.call_decorators, _Rewrite(container))
    else:
        # The container of the latest app set up on the broker, so a module-level broker keeps only one
        rewrite.container = container


def _app_bindings(app: FastStream | AsgiFastStream, container: Dependencies) -> list[Binding]:
    """
    The clients of every subscriber the brokers of `app` serve, routers included, each once.
    """
    subscribers = (subscriber for broker in app.brokers for subscriber in broker.subscribers)
    return unique(binding for subscriber in subscribers for binding in _subscriber_bindings(subscriber, container))


def _subscriber_bindings(subscriber: Any, container: Dependencies) -> Iterator[Binding]:
    # The dependencies of the broker and of the routers on the way
    outer = getattr(subscriber, "_outer_config", None)
    for depends in getattr(outer, "broker_dependencies", ()):
        yield from bind(depends.dependency, container, _FASTSTREAM)
    for call in subscriber.calls:
        yield from bind(_declared(call.handler), container, _FASTSTREAM)
        for depends in call.dependencies:
            yield from bind(depends.dependency, container, _FASTSTREAM)


def _declared(handler: Any) -> Callable[..., Any]:
    """
    The function a subscriber was declared with.
    """
    # FastStream 0.7 keeps it apart from the function composed on start; 0.6 keeps only `_original_call`,
    # which is the declared function for an `async def`
    declared = getattr(handler, "_declared_call", None)
    return handler._original_call if declared is None else declared  # type: ignore[no-any-return]
