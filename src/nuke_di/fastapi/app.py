"""
setup() of an app: its routes take clients from a container, which runs with the app.
"""

from functools import partial

from fastapi import FastAPI
from fastapi.routing import APIRoute

from nuke_di.core import DI, Dependencies
from nuke_di.fastapi.bindings import _APP_BINDINGS, _AppBindings
from nuke_di.fastapi.routes import _route_class
from nuke_di.fastapi.tracking import _include_router, _router_bindings, _track, _WebSocketAdder
from nuke_di.integration import wrap_lifespan
from nuke_di.naming import sname


def setup(app: FastAPI, container: Dependencies = DI) -> None:
    """
    Fill client arguments of the routes declared on `app` from now on, and run `container` with the app:
    connect it on startup, disconnect it on shutdown.
    """
    route_cls = _route_class(container)
    router = app.router
    current = router.route_class
    if not issubclass(current, route_cls):
        if current is not APIRoute:
            raise TypeError(
                f"app.router.route_class is {sname(current)}, which does not fill clients from this container, "
                f"and setup() would replace it: derive it from ClientRoute for DI"
            )
        router.route_class = route_cls

    bindings = _AppBindings(route_cls.container)
    # The app's own lifespan runs inside, so its startup and shutdown code can use the clients
    router.lifespan_context = wrap_lifespan(
        router.lifespan_context,
        route_cls.container,
        lambda: bindings.for_app(_router_bindings(router)),
    )
    setattr(app, _APP_BINDINGS, bindings)

    _track(router, route_cls)
    # On the router of the app rather than the app: app.include_router() calls it, and so may the user
    router.include_router = partial(_include_router, router, router.include_router, route_cls)  # type: ignore[method-assign]
    # FastAPI builds websocket routes without the route class
    router.add_api_websocket_route = _WebSocketAdder(router, router.add_api_websocket_route, route_cls)  # type: ignore[method-assign]
