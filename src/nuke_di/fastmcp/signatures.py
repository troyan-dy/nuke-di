"""
The signature rewrite of FastMCP: a `Depends(...)` default for every client argument of a function and of the
functions it depends on.
"""

import functools
import inspect
import operator
import weakref
from collections.abc import Mapping
from typing import Any, get_type_hints

from fastmcp.dependencies import Dependency, Depends

from nuke_di.core import Dependencies
from nuke_di.integration import Binding, Framework, client_of
from nuke_di.naming import sname

# FastMCP fills a parameter whose default is `Depends(fn)` by calling `fn`, and leaves it out of the schema; a
# `Depends` in `Annotated[...]` is entered but not passed, so the kit's `bind()` does not apply
_FASTMCP = Framework(
    name="FastMCP",
    not_started="{client} was not started with the server: add its tool, resource or prompt before the server starts",
    not_connected="{client} is not connected: run the server with its lifespan, e.g. `async with Client(server)`",
)

# The class of FastMCP's `Depends` markers
_DEPENDS: type[Any] = type(Depends(lambda: None))


def _bind(call: Any, container: Dependencies) -> list[Binding]:
    """
    Give every client argument of `call` and of the functions it depends on a `Depends(...)` default; return
    their bindings.
    """
    # A bound method keeps its signature on its function, as FastMCP's own rewrite does
    function = call.__func__ if inspect.ismethod(call) else call
    if not inspect.isfunction(function):
        _refuse(call)
        return []
    owner = function.__dict__.get("__nuke_di_owner__")
    if owner is not None and owner[1] is not _FASTMCP:
        # The kit's message, which it raises for the reverse order too
        raise TypeError(
            f"{sname(function)} takes clients in both {owner[1].name} and {_FASTMCP.name} handlers: nuke-di "
            f"rewrites its signature for one framework, so give each framework its own function"
        )
    if owner is not None:
        # Bound once, whatever the container: FastMCP caches a function's signature for good, so every server
        # that starts resolves the same bindings
        return function.__dict__["__nuke_di_bindings__"]  # type: ignore[no-any-return]
    try:
        hints = get_type_hints(function, include_extras=True)
    except NameError:
        # E.g. a name imported under TYPE_CHECKING: FastMCP reports what it cannot evaluate itself
        return []
    return _rewrite(function, hints, container)


def _rewrite(function: Any, hints: dict[str, Any], container: Dependencies) -> list[Binding]:
    signature = inspect.signature(function)
    rewrite = _Rewrite(container, hints)
    for argument in signature.parameters.values():
        rewrite.add(argument)

    if not rewrite.clients:
        # Nothing to rewrite here; the functions it depends on are bound once themselves
        return rewrite.found
    if _read_by_fastmcp(function):
        raise TypeError(
            f"FastMCP read {sname(function)} before setup() and keeps the signature it read for good: call setup() "
            f"before the function is first added to any server, and start the process again"
        )
    function.__signature__ = signature.replace(parameters=rewrite.arguments())
    function.__nuke_di_owner__ = (weakref.ref(container), _FASTMCP)
    function.__nuke_di_bindings__ = rewrite.found
    return rewrite.found


class _Rewrite:
    """
    The arguments of one signature as FastMCP is to read them, and the clients found on the way.
    """

    def __init__(self, container: Dependencies, hints: dict[str, Any]) -> None:
        self.container = container
        self.hints = hints
        self.found: list[Binding] = []
        # The arguments left as they are, and the client arguments with their `Depends(...)` default
        self.kept: list[inspect.Parameter] = []
        self.clients: list[inspect.Parameter] = []

    def add(self, argument: inspect.Parameter) -> None:
        if isinstance(argument.default, _DEPENDS):
            # `reader: Reader = Depends(current_reader)`: the function it depends on takes clients too
            self.found.extend(_bind(argument.default.factory, self.container))
        client = client_of(self.hints.get(argument.name, argument.annotation))
        if client is None or isinstance(argument.default, Dependency):
            # A `Depends(...)`, `CurrentContext()` or other marker of the user's own stays
            self.kept.append(argument)
            return
        binding = Binding(client, self.container, _FASTMCP)
        self.found.append(binding)
        # Keyword-only: a parameter with a default cannot precede one without, and FastMCP passes every argument
        # by name
        marker = Depends(binding.get)
        self.clients.append(argument.replace(kind=argument.KEYWORD_ONLY, default=marker))

    def arguments(self) -> list[inspect.Parameter]:
        """
        The arguments of the rewritten signature.
        """
        # Sorted by kind, which keeps the order within each kind: the clients go after the arguments the caller
        # passes, before `**kwargs`
        arguments = [*self.kept, *self.clients]
        return sorted(arguments, key=operator.attrgetter("kind"))


def _read_by_fastmcp(function: Any) -> bool:
    """
    Whether FastMCP, through uncalled-for, has cached the dependencies of `function` already.
    """
    try:
        from uncalled_for.introspection import _parameter_cache
    except ImportError:  # pragma: no cover - an uncalled-for that keeps its caches elsewhere
        return False
    return function in _parameter_cache


def _refuse(call: Any) -> None:
    """
    A `functools.partial`, a callable object or a class that takes clients: refuse it instead of letting FastMCP
    report the client as a type it does not know.
    """
    try:
        hints, names = _read(call)
    except (NameError, TypeError, ValueError):
        return
    for name in names:
        client = client_of(hints.get(name))
        if client is not None:
            shape = _shape(call)[1]
            raise TypeError(
                f'Argument "{name}" of {shape} is {sname(client)}: nuke-di fills the clients of a function or a bound '
                f"method in FastMCP, so declare a function instead"
            )


def _read(
    call: Any,
) -> tuple[dict[str, Any], Mapping[str, inspect.Parameter]]:
    """
    The type hints of what FastMCP calls for `call`, and the arguments of `call`.
    """
    hints = get_type_hints(_shape(call)[0], include_extras=True)
    return hints, inspect.signature(call).parameters


def _shape(call: Any) -> tuple[Any, str]:
    """
    What FastMCP calls for `call`, and how the error names `call`.
    """
    if isinstance(call, functools.partial):
        return call.func, f"a functools.partial of {sname(call.func)}"
    if inspect.isclass(call):
        return call.__init__, f"the class {sname(call)}"
    method = getattr(type(call), "__call__", None)  # noqa: B004  # the method itself, not a callable() check
    return method, f"the callable object {sname(type(call))}"
