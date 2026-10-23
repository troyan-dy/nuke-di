"""
What an integration fills: the client arguments of a function, each bound to the instance resolved on startup,
and what the users of a framework are told when one is missing.
"""

import weakref
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Annotated, Any, cast, get_args, get_origin

from nuke_di.core import Dependencies
from nuke_di.naming import sname
from nuke_di.types import NotSingletonClient, isnotsingleton


@dataclass(frozen=True, eq=False)
class Framework:
    """
    What the users of one framework are told when a client is missing; `{client}` is its class name.
    """

    name: str
    not_started: str
    not_connected: str


@dataclass(frozen=True, eq=False)
class DependsFramework(Framework):
    """
    A framework that injects through `Depends(...)` markers in signatures.
    """

    # The class of the markers
    depends: type
    # Builds the marker for a callable
    make_depends: Callable[[Callable[..., Any]], Any]
    # Whether a function is bound again for every container, for a framework that reads a signature once,
    # when the handler is declared, so each app keeps the bindings it captured. Otherwise a function is
    # bound once, and every app that starts resolves the same bindings
    per_container: bool = True
    # Internal, for nuke_di.fastapi: what the marker of a client argument calls, `binding.get` when not given.
    # FastAPI's is a function that takes the request and finds the client of the app it came to, since
    # FastAPI reads signatures lazily and serves one function from several apps at once
    _getter: Callable[["Binding"], Callable[..., Any]] | None = None


class Binding:
    """
    A client argument of one function, filled with the instance resolved on startup.
    """

    def __init__(self, client_class: type[NotSingletonClient], container: Dependencies, framework: Framework) -> None:
        self.client_class = client_class
        # The container that resolves it, or would: it tells "not started" from "not connected"
        self.container = weakref.ref(container)
        self.framework = framework
        self.instance: NotSingletonClient | None = None

    def require(self) -> NotSingletonClient:
        """
        The instance resolved on startup; without one, the framework's "not started" or "not connected" error.
        """
        if self.instance is not None:
            return self.instance

        container = self.container()
        if container is not None and container.connected:
            # The app started, but its startup never saw this function
            raise RuntimeError(self.framework.not_started.format(client=sname(self.client_class)))
        raise RuntimeError(self.framework.not_connected.format(client=sname(self.client_class)))

    async def get(self) -> NotSingletonClient:
        """
        `require()` as the framework calls it: with no arguments and `async`, so inline rather than in a threadpool.
        """
        return self.require()


def unique(bindings: Iterable[Binding]) -> list[Binding]:
    """
    `bindings` without repeats, in order: one function is often reachable from several handlers.
    """
    return list({id(binding): binding for binding in bindings}.values())


def client_of(hint: Any, *markers: type) -> type[NotSingletonClient] | None:
    """
    The client a type hint asks for: `Client`, or `Annotated[Client, ...]` without any of `markers`.
    """
    if isnotsingleton(hint):
        return cast(type[NotSingletonClient], hint)
    if get_origin(hint) is not Annotated:
        return None
    inner, *metadata = get_args(hint)
    if not isnotsingleton(inner):
        return None
    marked = any(isinstance(extra, markers) for extra in metadata)
    return None if marked else cast(type[NotSingletonClient], inner)
