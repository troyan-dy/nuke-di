"""
A lifespan for an app whose framework has no dependency injection: Starlette, Quart, aiohttp, or a FastAPI app
that keeps its signatures. It connects the clients it lists on startup and disconnects them on shutdown, and a
handler takes one with `get()`. No framework is imported.

See docs/specs/asgi.md and docs/guide/asgi.md.
"""

from contextlib import AbstractAsyncContextManager
from typing import TypeVar, cast

from nuke_di.core import Dependencies, isnotsingleton
from nuke_di.integration import Binding, Framework, running
from nuke_di.types import NotSingletonClient
from nuke_di.utils import sname

__all__ = ("Lifespan", "lifespan")

CT = TypeVar("CT", bound=NotSingletonClient)

_ASGI = Framework(
    name="ASGI",
    not_started="{client} is not a client of this lifespan: list it in `lifespan(container, ...)`",
    not_connected=(
        "{client} is not connected: start the app with its lifespan, e.g. `with TestClient(app)` in Starlette "
        "or `async with app.test_app()` in Quart"
    ),
)


class Lifespan:
    """
    The lifespan of an app, `Starlette(lifespan=clients)`, and where its handlers take the clients it lists,
    `clients.get(UserService)`. `lifespan(container, *clients)` makes one.
    """

    def __init__(self, container: Dependencies, *clients: type[NotSingletonClient]) -> None:
        if not isinstance(container, Dependencies):
            raise TypeError(
                f"lifespan() takes the container first, then the clients, e.g. lifespan(DI, Database); "
                f"got {container!r}"
            )
        for cls in clients:
            # The class itself, as resolve() takes it: not `Annotated[Database, ...]`
            if not isnotsingleton(cls):
                raise TypeError(f"{cls!r} is not a client: subclass Client or NotSingletonClient")
        self._container = container
        # Resolved on every startup, not now: a test replaces a client with override() before the app starts
        self._bindings = {cls: Binding(cls, container, _ASGI) for cls in clients}

    def __call__(self, app: object = None) -> AbstractAsyncContextManager[None]:
        """
        Resolve the clients and connect the container until the block exits; `app` is not used, it is what a
        framework passes to its lifespan.
        """
        return running(self._container, list(self._bindings.values()))

    def get(self, cls: type[CT]) -> CT:
        """
        The client of `cls` that connected on startup, or the Replacement registered for it.
        """
        binding = self._bindings.get(cls)
        if binding is None:
            # Not looked up in the container: a client that is only a dependency of a listed one would work
            # until that one stops depending on it
            raise RuntimeError(_ASGI.not_started.format(client=sname(cls)))
        if binding.instance is None:
            raise RuntimeError(_ASGI.not_connected.format(client=sname(cls)))
        return cast(CT, binding.instance)


def lifespan(container: Dependencies, *clients: type[NotSingletonClient]) -> Lifespan:
    """
    A lifespan that connects `clients` and every client they depend on for the time the app runs, for a
    framework that calls `lifespan(app)` and enters the async context manager it returns, as Starlette does.
    """
    return Lifespan(container, *clients)
