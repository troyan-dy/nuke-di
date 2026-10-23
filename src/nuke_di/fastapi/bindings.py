"""
The clients of the routes: bound once per function, and found per request in the app the request came to.
"""

import weakref
from collections.abc import Callable, Iterable
from typing import Any, ClassVar, TypeAlias

from fastapi import Depends, params
from fastapi.routing import APIRoute
from starlette.requests import HTTPConnection

from nuke_di.core import Dependencies
from nuke_di.integration import Binding, DependsFramework, bind
from nuke_di.naming import sname

# Where an app set up by setup() keeps its _AppBindings
_APP_BINDINGS = "_nuke_di_app_bindings"

# Every app's own Binding of each shared one
_Copies: TypeAlias = weakref.WeakKeyDictionary[Binding, weakref.WeakSet[Binding]]


class _AppBindings:
    """
    The clients one app serves, resolved in its own container.

    A function is bound once, whatever the container: FastAPI 0.137+ reads the signatures of included routes
    lazily, on the app's first request, so a signature rewritten again for another container would reach the
    routes of every app. Each app instead keeps its own `Binding` for every shared one, and the getter in the
    signature takes the one of the app the request came to.
    """

    # Every app's own Binding of each shared one, for a request to an app that has none of its own: the
    # routes of a set-up app served by another app object, a mounted app, an app without setup()
    _copies: ClassVar[_Copies] = weakref.WeakKeyDictionary()

    def __init__(self, container: Dependencies) -> None:
        self.container = container
        # Kept from one start of the app to the next: replaced, they would leave a running app without them
        self.bindings: dict[Binding, Binding] = {}

    def for_app(self, shared: list[Binding]) -> list[Binding]:
        """
        The app's own `Binding` of each of `shared`, for its startup to resolve.
        """
        for binding in shared:
            if binding not in self.bindings:
                own = Binding(binding.client_class, self.container, _FASTAPI)
                self.bindings[binding] = own
                self._copies.setdefault(binding, weakref.WeakSet()).add(own)
        return [self.bindings[each] for each in shared]

    @classmethod
    def serving(cls, app: object, shared: Binding) -> Binding:
        """
        The `Binding` that fills `shared` in a request to `app`.
        """
        bindings: _AppBindings | None = getattr(app, _APP_BINDINGS, None)
        own = None if bindings is None else bindings.bindings.get(shared)
        if own is not None:
            return own
        # Not its own: the one app that filled it, e.g. the set-up app whose routes another app serves
        copies = cls._copies.get(shared, ())
        filled = [copy for copy in copies if copy.instance is not None]
        if len(filled) == 1:
            return filled[0]
        if bindings is None:
            # None, or one per running container: an app without setup() cannot tell which is its own
            raise RuntimeError(_FASTAPI.not_connected.format(client=sname(shared.client_class)))
        # Raises "not started" while the app's container is connected, "not connected" otherwise
        return Binding(shared.client_class, bindings.container, _FASTAPI)


class _Getter:
    """
    What the marker of a client argument calls: it takes the request and finds the client of the app it came to.
    """

    def __init__(self, binding: Binding) -> None:
        self.binding = binding

    async def __call__(self, connection: HTTPConnection) -> Any:
        # `async`, so FastAPI calls it inline rather than in a threadpool; the connection is a request or a
        # websocket, and its app the one that serves it, a mounted app included
        return _AppBindings.serving(connection.app, self.binding).require()


_FASTAPI = DependsFramework(
    name="FastAPI",
    depends=params.Depends,
    make_depends=Depends,
    not_started=(
        "{client} was not started with the app: include the router of its route into the app "
        "or into a ClientRouter, not into a plain APIRouter"
    ),
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `with TestClient(app)`",
    # One signature for every container, read whenever FastAPI likes; the app is chosen per request
    per_container=False,
    _getter=_Getter,
)


class _ContainerRoute(APIRoute):
    """
    Route class that fills the arguments typed as clients from `container`; one subclass per container.
    """

    container: ClassVar[Dependencies]

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        # Before FastAPI reads the signatures, which happens in APIRoute.__init__
        self.nuke_di_bindings = _bind_route(endpoint, kwargs, type(self))
        super().__init__(path, endpoint, **kwargs)


def _bind_route(
    endpoint: Callable[..., Any],
    kwargs: dict[str, Any],
    route_cls: type[_ContainerRoute],
) -> list[Binding]:
    """
    The clients of a route's endpoint and of the `dependencies=` it is declared with.
    """
    dependencies = _bind_dependencies(kwargs.get("dependencies") or (), route_cls)
    return [*_bind(endpoint, route_cls), *dependencies]


def _bind_dependencies(dependencies: Iterable[params.Depends], route_cls: type[_ContainerRoute]) -> list[Binding]:
    """
    The clients of `dependencies=`, given to a route, a router or `include_router()`.
    """
    return [binding for depends in dependencies for binding in _bind(depends.dependency, route_cls)]


def _bind(
    call: Callable[..., Any] | None,
    route_cls: type[_ContainerRoute],
) -> list[Binding]:
    return bind(call, route_cls.container, _FASTAPI)
