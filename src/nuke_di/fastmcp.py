"""
FastMCP integration (`fastmcp` 4.x): the tools, resources and prompts of a `FastMCP` server, and the functions
they depend on, take clients by type hint.

See docs/specs/fastmcp.md and docs/adr/0011-mcp-clients-through-the-sdk-markers.md.
"""

import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from nuke_di.core import DI, Dependencies
from nuke_di.integration import Binding, Framework, client_of, unique, wrap_lifespan

__all__ = ("setup",)

# FastMCP fills a parameter whose default is `Depends(fn)` by calling `fn`, and leaves it out of the schema; a
# `Depends` in `Annotated[...]` is entered but not passed, so the kit's `bind()` does not apply
_FASTMCP = Framework(
    name="FastMCP",
    not_started=("{client} was not started with the server: add its tool, resource or prompt before the server starts"),
    not_connected="{client} is not connected: run the server with its lifespan, e.g. `async with Client(server)`",
)


def _noop() -> None: ...  # pragma: no cover


# The class of FastMCP's markers
_DEPENDS: type[Any] = type(Depends(_noop))

# Where FastMCP takes the functions of a server: its decorators come here too
_REGISTRATIONS = ("add_tool", "add_resource", "add_prompt")


def setup(server: FastMCP[Any], container: Dependencies = DI) -> None:
    """
    Fill client arguments of the tools, resources and prompts added to `server` from now on, and run `container`
    with the server: connect it when the server's lifespan starts, disconnect it when the lifespan ends.
    """
    bindings: list[Binding] = []
    # The lifespan FastMCP enters once for every transport and the in-memory `Client`; the server's own lifespan
    # runs inside, so it can use the clients
    server._lifespan = wrap_lifespan(server._lifespan, container, lambda: unique(bindings))
    provider = server.local_provider
    for name in _REGISTRATIONS:
        setattr(provider, name, _Register(getattr(provider, name), container, bindings))


class _Register:
    """
    Rewrites a function before FastMCP reads its signature, which it does when the function is added.
    """

    def __init__(self, add: Callable[[Any], Any], container: Dependencies, bindings: list[Binding]) -> None:
        self.add = add
        self.container = container
        self.bindings = bindings

    def __call__(self, component: Any) -> Any:
        # A Tool, Resource or Prompt object was built from its function already
        self.bindings += _bind(component, self.container)
        return self.add(component)


def _bind(call: Any, container: Dependencies) -> list[Binding]:
    """
    Give every client argument of `call` and of the functions it depends on a `Depends(...)` default; return
    their bindings.
    """
    # A bound method keeps its signature on its function, as FastMCP's own rewrite does
    function = call.__func__ if inspect.ismethod(call) else call
    if not inspect.isfunction(function):
        return []
    bound: list[Binding] | None = vars(function).get("__nuke_di_bindings__")
    if bound is not None:
        # Bound once, whatever the container: FastMCP caches a function's signature for good, so every server
        # that starts resolves the same bindings
        return bound
    try:
        hints = get_type_hints(function, include_extras=True)
    except NameError:
        # E.g. a name imported under TYPE_CHECKING: FastMCP reports what it cannot evaluate itself
        return []

    signature = inspect.signature(function)
    found: list[Binding] = []
    kept: list[inspect.Parameter] = []
    clients: list[inspect.Parameter] = []
    for param in signature.parameters.values():
        if isinstance(param.default, _DEPENDS):
            # `reader: Reader = Depends(current_reader)`: the function it depends on takes clients too
            found += _bind(param.default.factory, container)
        client = client_of(hints.get(param.name, param.annotation))
        if client is None or param.default is not param.empty:
            kept.append(param)
            continue
        binding = Binding(client, container, _FASTMCP)
        found.append(binding)
        # Keyword-only: a parameter with a default cannot precede one without, and FastMCP passes every argument
        # by name
        clients.append(param.replace(kind=param.KEYWORD_ONLY, default=Depends(binding.get)))

    if clients:
        # Sorted by kind, which keeps the order within each kind: the clients go after the arguments the caller
        # passes, before `**kwargs`
        parameters = sorted([*kept, *clients], key=lambda param: param.kind)
        function.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    function.__nuke_di_bindings__ = found  # type: ignore[attr-defined]
    return found
