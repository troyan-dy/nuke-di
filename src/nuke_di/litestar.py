"""
Litestar integration: route handlers and their dependencies take clients by type hint.

See docs/specs/litestar.md and docs/adr/0004-litestar-clients-by-name.md.
"""

import inspect
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated, Any, get_type_hints

from litestar import Litestar, Router
from litestar.config.app import AppConfig
from litestar.constants import RESERVED_KWARGS
from litestar.di import Provide
from litestar.handlers import BaseRouteHandler, HTTPRouteHandler, WebsocketRouteHandler
from litestar.handlers.websocket_handlers import WebsocketListenerRouteHandler
from litestar.plugins import InitPlugin
from litestar.routes import HTTPRoute

from nuke_di.core import DI, Dependencies
from nuke_di.integration import Binding, Framework, client_of, running
from nuke_di.types import NotSingletonClient
from nuke_di.utils import sname

__all__ = ("ClientPlugin",)

# Litestar has no `Depends`: it matches dependencies by name, see `_Clients`
_LITESTAR = Framework(
    name="Litestar",
    not_started="{client} was not started with the app: register its handler when the app is created",
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `with TestClient(app)`",
)

# What a client argument is annotated with for Litestar: an explicit dependency, provided by name, whose
# value is not validated against the type, so that any Replacement passes
try:
    from litestar.di import Dependency
    from litestar.params import SkipValidationMarker

    _MARKERS: tuple[Any, ...] = (Dependency(), SkipValidationMarker())
except ImportError:  # pragma: no cover - Litestar before 2.23, checked by `make test-litestar-min`
    from litestar.params import Dependency as DependencyKwarg

    _MARKERS = (DependencyKwarg(skip_validation=True),)


class ClientPlugin(InitPlugin):
    """
    Fill client arguments of the route handlers the app is created with, and run `container` with the app:
    connect it on startup, disconnect it after the app's own shutdown hooks.
    """

    def __init__(self, container: Dependencies = DI) -> None:
        self.container = container

    def on_app_init(self, app_config: AppConfig) -> AppConfig:
        if any(getattr(lifespan, "__nuke_di__", False) for lifespan in app_config.lifespan):
            raise TypeError("ClientPlugin was already added to this app")
        clients = _Clients(app_config.dependencies)
        # A router of our own lays out every handler the app is created with: those of nested routers and
        # controllers too. Registering into it copies them and parses nothing, and it is dropped afterwards
        for route in Router(path="/", route_handlers=app_config.route_handlers).routes:
            for handler in route.route_handlers if isinstance(route, HTTPRoute) else [route.route_handler]:
                clients.add_handler(handler)

        container = self.container
        bindings = {name: Binding(cls, container, _LITESTAR) for name, cls in clients.found.items()}
        # On the app, so every layer below sees them, and under the dependencies of the app, which win
        app_config.dependencies = {
            **{name: Provide(binding.get) for name, binding in bindings.items()},
            **app_config.dependencies,
        }

        # Entered on startup, closed after the shutdown hooks, which Litestar calls after every lifespan exits
        stack = AsyncExitStack()

        @asynccontextmanager
        async def lifespan(app: Litestar) -> AsyncIterator[None]:
            await stack.enter_async_context(running(container, list(bindings.values())))
            yield

        async def disconnect() -> None:
            await stack.aclose()

        # The outermost lifespan, so the app's own lifespans and startup hooks see connected clients
        lifespan.__nuke_di__ = True  # type: ignore[attr-defined]
        app_config.lifespan.insert(0, lifespan)
        app_config.on_shutdown.append(disconnect)
        return app_config


class _Clients:
    """
    The client arguments of an app's handlers and of every dependency they declare, by name: Litestar
    provides dependencies by name, and builds a dependency once for all the handlers below its layer.
    """

    def __init__(self, app_dependencies: dict[str, Any]) -> None:
        self.app_dependencies = app_dependencies
        self.found: dict[str, type[NotSingletonClient]] = {}
        # Where each name was found first, for the error about a name taken by two clients
        self.places: dict[str, Any] = {}

    def add_handler(self, handler: BaseRouteHandler) -> None:
        if isinstance(handler, WebsocketListenerRouteHandler):
            # Litestar reads the signature of a listener when it is declared, before nuke-di sees it
            self._refuse_listener(handler)
            return
        if not isinstance(handler, HTTPRouteHandler | WebsocketRouteHandler):
            # ASGI handlers take no dependencies
            return

        # The dependencies the handler sees, from the app down to the handler; a name among them is not ours
        layers = [self.app_dependencies, *(layer.dependencies or {} for layer in handler.ownership_layers)]
        provided = {name: provider for dependencies in layers for name, provider in dependencies.items()}
        for call in [handler.fn, *(getattr(provider, "dependency", provider) for provider in provided.values())]:
            self._visit(call, provided)

    def _visit(self, call: Any, provided: dict[str, Any]) -> None:
        """
        Record the client arguments of `call` and annotate them as dependencies for Litestar.
        """
        if inspect.isclass(call):
            function = call.__init__
        elif inspect.ismethod(call):
            # E.g. the handler of a controller
            function = call.__func__
        else:
            function = call
        if not inspect.isfunction(function):
            # Callable objects: no annotations to replace in place
            return
        try:
            hints = get_type_hints(function, include_extras=True)
        except NameError:
            # E.g. a name imported under TYPE_CHECKING, or the OPTIONS handler Litestar adds: Litestar reports
            # what it cannot evaluate itself
            return

        for name in inspect.signature(call).parameters:
            client = client_of(hints.get(name))
            if client is None:
                continue
            if name in RESERVED_KWARGS:
                raise TypeError(
                    f'Argument "{name}" of {sname(call)} is {sname(client)}, but Litestar reserves the name "{name}": '
                    f"rename it"
                )
            if name not in provided:
                self._record(name, client, call)
            # Litestar would read the client from the query string otherwise, and warns about a dependency
            # matched by name without a marker since 2.23
            function.__annotations__[name] = Annotated[(client, *_MARKERS)]

    def _record(self, name: str, client: type[NotSingletonClient], call: Any) -> None:
        recorded = self.found.setdefault(name, client)
        place = self.places.setdefault(name, call)
        if recorded is not client:
            raise TypeError(
                f'Argument "{name}" is {sname(recorded)} in {sname(place)} and {sname(client)} in {sname(call)}: '
                f"Litestar provides dependencies by name, so give different clients different names"
            )

    def _refuse_listener(self, handler: WebsocketListenerRouteHandler) -> None:
        for name, field in handler.parsed_fn_signature.parameters.items():
            client = client_of(field.annotation)
            if client is not None:
                raise TypeError(
                    f'Argument "{name}" of the websocket listener {handler} is {sname(client)}: a listener takes '
                    f"no clients, use a @websocket handler instead"
                )
