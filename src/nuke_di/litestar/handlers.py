"""
The client arguments of the handlers an app is created with and of their dependencies, found by name and
annotated as dependencies for Litestar.
"""

import inspect
from typing import Annotated, Any, get_type_hints

from litestar.constants import RESERVED_KWARGS
from litestar.handlers import BaseRouteHandler, HTTPRouteHandler, WebsocketRouteHandler
from litestar.handlers.websocket_handlers import WebsocketListenerRouteHandler

from nuke_di.integration import client_of
from nuke_di.naming import sname
from nuke_di.types import NotSingletonClient


def _markers() -> tuple[Any, ...]:
    """
    What a client argument is annotated with for Litestar: an explicit dependency, provided by name, whose
    value is not validated against the type, so that any Replacement passes.
    """
    try:
        return _current_markers()
    except ImportError:  # pragma: no cover - Litestar before 2.23, checked by `make test-litestar-min`
        from litestar.params import Dependency as DependencyKwarg

        return (DependencyKwarg(skip_validation=True),)


def _current_markers() -> tuple[Any, ...]:
    from litestar.di import Dependency
    from litestar.params import SkipValidationMarker

    return (Dependency(), SkipValidationMarker())


_MARKERS = _markers()


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

        provided = self._provided(handler)
        providers = tuple(getattr(provider, "dependency", provider) for provider in provided.values())
        for call in (handler.fn, *providers):
            self._visit(call, provided)

    def _provided(self, handler: HTTPRouteHandler | WebsocketRouteHandler) -> dict[str, Any]:
        """
        The dependencies the handler sees, from the app down to the handler; a name among them is not ours.
        """
        layers = [self.app_dependencies, *(layer.dependencies or {} for layer in handler.ownership_layers)]
        return {name: provider for dependencies in layers for name, provider in dependencies.items()}

    def _visit(self, call: Any, provided: dict[str, Any]) -> None:
        """
        Record the client arguments of `call` and annotate them as dependencies for Litestar.
        """
        function = _function_of(call)
        if function is None:
            return
        try:
            hints = get_type_hints(function, include_extras=True)
        except NameError:
            # E.g. a name imported under TYPE_CHECKING, or the OPTIONS handler Litestar adds: Litestar reports
            # what it cannot evaluate itself
            return

        for name in inspect.signature(call).parameters:
            client = client_of(hints.get(name))
            if client is not None:
                self._add_client(call, name, client, provided)
                # Litestar would read the client from the query string otherwise, and warns about a dependency
                # matched by name without a marker since 2.23
                function.__annotations__[name] = Annotated[(client, *_MARKERS)]

    def _add_client(
        self,
        call: Any,
        name: str,
        client: type[NotSingletonClient],
        provided: dict[str, Any],
    ) -> None:
        if name in RESERVED_KWARGS:
            raise TypeError(
                f'Argument "{name}" of {sname(call)} is {sname(client)}, but Litestar reserves the name "{name}": '
                f"rename it"
            )
        if name not in provided:
            self._record(name, client, call)

    def _record(self, name: str, client: type[NotSingletonClient], call: Any) -> None:
        recorded = self.found.setdefault(name, client)
        place = self.places.setdefault(name, call)
        if recorded is not client:
            first = f"{sname(recorded)} in {sname(place)}"
            second = f"{sname(client)} in {sname(call)}"
            raise TypeError(
                f'Argument "{name}" is {first} and {second}: '
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


def _function_of(call: Any) -> Any:
    """
    The function whose annotations Litestar reads for `call`, or `None` for a callable object, which has no
    annotations to replace in place.
    """
    if inspect.isclass(call):
        function = call.__init__
    elif inspect.ismethod(call):
        # E.g. the handler of a controller
        function = call.__func__
    else:
        function = call
    return function if inspect.isfunction(function) else None
