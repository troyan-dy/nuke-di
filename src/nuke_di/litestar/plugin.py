"""
The plugin: the clients of the handlers provided on the app, and the container run with the app.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager

from litestar import Litestar, Router
from litestar.config.app import AppConfig
from litestar.di import Provide
from litestar.handlers import BaseRouteHandler
from litestar.plugins import InitPlugin
from litestar.routes import HTTPRoute

from nuke_di.core import DI, Dependencies
from nuke_di.integration import Binding, Framework, running
from nuke_di.litestar.handlers import _Clients

# Litestar has no `Depends`: it matches dependencies by name, see `_Clients`
_LITESTAR = Framework(
    name="Litestar",
    not_started="{client} was not started with the app: register its handler when the app is created",
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `with TestClient(app)`",
)


class ClientPlugin(InitPlugin):
    """
    Fill client arguments of the route handlers the app is created with, and run `container` with the app:
    connect it on startup, disconnect it after the app's own shutdown hooks.
    """

    def __init__(self, container: Dependencies = DI) -> None:
        self.container = container

    def on_app_init(self, app_config: AppConfig) -> AppConfig:
        if any(isinstance(lifespan, _Lifespan) for lifespan in app_config.lifespan):
            raise TypeError("ClientPlugin was already added to this app")
        clients = _Clients(app_config.dependencies)
        for handler in _route_handlers(app_config):
            clients.add_handler(handler)

        found = clients.found.items()
        bindings = {name: Binding(client_class, self.container, _LITESTAR) for name, client_class in found}
        _provide(app_config, bindings)
        lifespan = _Lifespan(self.container, list(bindings.values()))
        # The outermost lifespan, so the app's own lifespans and startup hooks see connected clients
        app_config.lifespan.insert(0, lifespan)
        app_config.on_shutdown.append(lifespan.disconnect)
        return app_config


class _Lifespan:
    """
    The container's run with the app: entered on startup, and closed after the shutdown hooks, which Litestar
    calls after every lifespan exits.
    """

    def __init__(self, container: Dependencies, bindings: list[Binding]) -> None:
        self.container = container
        self.bindings = bindings
        self.stack = AsyncExitStack()

    @asynccontextmanager
    async def __call__(self, app: Litestar) -> AsyncIterator[None]:
        await self.stack.enter_async_context(running(self.container, self.bindings))
        yield

    async def disconnect(self) -> None:
        await self.stack.aclose()


def _route_handlers(app_config: AppConfig) -> Iterator[BaseRouteHandler]:
    """
    Every handler the app is created with, those of nested routers and controllers too: a router of our own lays
    them out. Registering into it copies them and parses nothing, and it is dropped afterwards.
    """
    for route in Router(path="/", route_handlers=app_config.route_handlers).routes:
        if isinstance(route, HTTPRoute):
            yield from route.route_handlers
        else:
            yield route.route_handler


def _provide(app_config: AppConfig, bindings: dict[str, Binding]) -> None:
    # On the app, so every layer below sees them, and under the dependencies of the app, which win
    provided = {name: Provide(binding.get) for name, binding in bindings.items()}
    app_config.dependencies = {**provided, **app_config.dependencies}
