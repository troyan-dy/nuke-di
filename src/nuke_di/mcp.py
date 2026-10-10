"""
MCP SDK integration (`mcp` 2.x): the tools of an `MCPServer` and the resolvers they use take clients by type hint.

The `Resolve` markers are in the annotations only while the SDK's `add_tool` reads them, which it does without
awaiting anything; adding one function from two threads at once is not supported.

See docs/specs/mcp.md and docs/adr/0011-mcp-clients-through-the-sdk-markers.md.
"""

import functools
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from mcp.server.mcpserver import MCPServer, Resolve
from mcp.server.mcpserver.exceptions import InvalidSignature

from nuke_di.core import DI, Dependencies
from nuke_di.integration import Binding, DependsFramework, client_of, unique, wrap_lifespan
from nuke_di.utils import sname

__all__ = ("setup",)

# The SDK fills a parameter annotated `Annotated[T, Resolve(fn)]` by calling `fn`, and leaves it out of the
# tool's input schema. A `DependsFramework` only so that `check()` runs its case of a resolver that takes a
# client; `bind()` does not apply, see `_annotate`
_MCP = DependsFramework(
    name="MCP",
    depends=Resolve,
    make_depends=Resolve,
    not_started="{client} was not started with the server: add its tool before the server starts",
    not_connected="{client} is not connected: run the server with its lifespan, e.g. `async with Client(server)`",
)

# A function's annotations as they were, entry by entry: (function, name, annotation)
_Saved = list[tuple[Any, str, Any]]


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
            try:
                add_tool(fn, *args, **kwargs)
            except InvalidSignature as exc:
                if found and "InputRequiredResult" in str(exc):
                    raise TypeError(
                        f"{sname(fn)} takes clients and returns an InputRequiredResult: nuke-di fills clients "
                        f"through the SDK's Resolve(...), which the SDK does not combine with an InputRequiredResult "
                        f"of the tool itself; such a tool takes no clients"
                    ) from exc
                raise
        bindings.extend(found)

    server.add_tool = add  # type: ignore[method-assign]


@contextmanager
def _annotated(fn: Callable[..., Any], container: Dependencies) -> Iterator[list[Binding]]:
    """
    Annotate the client arguments of `fn` and of the resolvers it uses with a `Resolve` marker for the time the
    SDK reads them; yield their bindings. The functions are left as they were written.
    """
    found: list[Binding] = []
    saved: _Saved = []
    try:
        _annotate(fn, container, found, saved, set())
        yield found
    finally:
        # In place, entry by entry: assigning `__annotations__` would drop `__annotate__` on Python 3.14
        for function, name, annotation in reversed(saved):
            function.__annotations__[name] = annotation


def _annotate(
    call: Callable[..., Any], container: Dependencies, found: list[Binding], saved: _Saved, seen: set[int]
) -> None:
    function = _annotated_function(call)
    if function is None or id(function) in seen:
        # A cycle of resolvers is the SDK's to report
        return
    seen.add(id(function))
    try:
        hints = get_type_hints(function, include_extras=True)
    except NameError:
        # E.g. a name imported under TYPE_CHECKING: the SDK reports what it cannot evaluate itself
        return

    annotations = function.__annotations__
    for name in inspect.signature(call).parameters:
        hint = hints.get(name)
        resolver = _resolver(hint)
        if resolver is not None:
            # `Annotated[Reader, Resolve(current_reader)]`: a resolver takes clients too
            _annotate(resolver, container, found, saved, seen)
            continue
        client = client_of(hint)
        if client is not None:
            binding = Binding(client, container, _MCP)
            found.append(binding)
            saved.append((function, name, annotations[name]))
            annotations[name] = Annotated[hint, Resolve(binding.get)]


def _annotated_function(call: Callable[..., Any]) -> Any:
    """
    The function whose annotations the SDK reads for `call`, or `None` when there is none to annotate.
    """
    if inspect.ismethod(call):
        # A bound method keeps its annotations on its function
        return call.__func__
    if inspect.isfunction(call):
        return call
    if isinstance(call, functools.partial):
        _refuse_partial(call)
        return None
    # A callable object: the SDK reads the annotations of its class's `__call__`
    method = getattr(type(call), "__call__", None)  # noqa: B004  # the method itself, not a callable() check
    return method if inspect.isfunction(method) else None


def _refuse_partial(call: functools.partial[Any]) -> None:
    try:
        hints = get_type_hints(call.func, include_extras=True)
    except NameError:
        return
    for name in inspect.signature(call).parameters:
        client = client_of(hints.get(name))
        if client is not None:
            raise TypeError(
                f'Argument "{name}" of a functools.partial of {sname(call.func)} is {sname(client)}: nuke-di fills '
                f"the clients of a function, a bound method or a callable object, so declare a function instead"
            )


def _resolver(hint: Any) -> Callable[..., Any] | None:
    """
    The function of the `Resolve` marker in an `Annotated[T, Resolve(fn)]` hint.
    """
    if get_origin(hint) is not Annotated:
        return None
    marker = next((item for item in get_args(hint)[1:] if isinstance(item, Resolve)), None)
    return None if marker is None else marker.fn
