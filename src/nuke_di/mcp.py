"""
MCP SDK integration (`mcp` 2.x): the tools of an `MCPServer` and the resolvers they use take clients by type hint.

See docs/specs/mcp.md and docs/adr/0011-mcp-clients-through-the-sdk-markers.md.
"""

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from mcp.server.mcpserver import MCPServer, Resolve

from nuke_di.core import DI, Dependencies
from nuke_di.integration import Binding, DependsFramework, client_of, unique, wrap_lifespan

__all__ = ("setup",)

# The SDK fills a parameter annotated `Annotated[T, Resolve(fn)]` by calling `fn`, and leaves it out of the
# tool's input schema
_MCP = DependsFramework(
    name="MCP",
    depends=Resolve,
    make_depends=Resolve,
    not_started="{client} was not started with the server: add its tool before the server starts",
    not_connected="{client} is not connected: run the server with its lifespan, e.g. `async with Client(server)`",
)


def setup(server: MCPServer, container: Dependencies = DI) -> None:
    """
    Fill client arguments of the tools added to `server` from now on, and run `container` with the server:
    connect it when the server's lifespan starts, disconnect it when the lifespan ends.
    """
    bindings: list[Binding] = []
    # The lifespan every transport and the in-memory `Client` enter; the server's own lifespan runs inside, so
    # it can use the clients
    lowlevel = server._lowlevel_server
    lowlevel.lifespan = wrap_lifespan(lowlevel.lifespan, container, lambda: unique(bindings))
    add_tool = server.add_tool

    def add(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        # `@server.tool()` comes here too; the SDK reads the function's annotations once, when the tool is added
        with _annotated(fn, container) as found:
            add_tool(fn, *args, **kwargs)
        bindings.extend(found)

    server.add_tool = add  # type: ignore[method-assign]


@contextmanager
def _annotated(fn: Callable[..., Any], container: Dependencies) -> Iterator[list[Binding]]:
    """
    Annotate the client arguments of `fn` and of the resolvers it uses with a `Resolve` marker for the time the
    SDK reads them; yield their bindings. The functions are left as they were written.
    """
    found: list[Binding] = []
    originals: list[tuple[Any, dict[str, Any]]] = []
    try:
        _annotate(fn, container, found, originals, set())
        yield found
    finally:
        for function, annotations in originals:
            function.__annotations__ = annotations


def _annotate(
    call: Callable[..., Any],
    container: Dependencies,
    found: list[Binding],
    originals: list[tuple[Any, dict[str, Any]]],
    seen: set[int],
) -> None:
    # A bound method keeps its annotations on its function
    function = call.__func__ if inspect.ismethod(call) else call
    if not inspect.isfunction(function) or id(function) in seen:
        # Callable objects: no annotations to replace; a cycle of resolvers is the SDK's to report
        return
    seen.add(id(function))
    try:
        hints = get_type_hints(function, include_extras=True)
    except NameError:
        # E.g. a name imported under TYPE_CHECKING: the SDK reports what it cannot evaluate itself
        return

    markers: dict[str, Any] = {}
    for name in inspect.signature(call).parameters:
        hint = hints.get(name)
        resolver = _resolver(hint)
        if resolver is not None:
            # `Annotated[Reader, Resolve(current_reader)]`: a resolver takes clients too
            _annotate(resolver, container, found, originals, seen)
            continue
        client = client_of(hint)
        if client is not None:
            binding = Binding(client, container, _MCP)
            found.append(binding)
            markers[name] = Annotated[hint, Resolve(binding.get)]

    if markers:
        originals.append((function, function.__annotations__))
        function.__annotations__ = {**function.__annotations__, **markers}


def _resolver(hint: Any) -> Callable[..., Any] | None:
    """
    The function of the `Resolve` marker in an `Annotated[T, Resolve(fn)]` hint.
    """
    if get_origin(hint) is not Annotated:
        return None
    marker = next((item for item in get_args(hint)[1:] if isinstance(item, Resolve)), None)
    return None if marker is None else marker.fn
