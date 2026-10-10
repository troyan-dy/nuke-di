"""
FastMCP integration (`fastmcp` 4.x): the tools, resources and prompts of a `FastMCP` server, and the functions
they depend on, take clients by type hint.

See docs/specs/mcp.md and docs/adr/0011-mcp-clients-through-the-sdk-markers.md.
"""

import functools
import inspect
import weakref
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from fastmcp import FastMCP
from fastmcp.dependencies import Dependency, Depends

from nuke_di.core import DI, Dependencies
from nuke_di.integration import Binding, Framework, client_of, running, unique
from nuke_di.utils import sname

__all__ = ("setup",)

# FastMCP fills a parameter whose default is `Depends(fn)` by calling `fn`, and leaves it out of the schema; a
# `Depends` in `Annotated[...]` is entered but not passed, so the kit's `bind()` does not apply
_FASTMCP = Framework(
    name="FastMCP",
    not_started="{client} was not started with the server: add its tool, resource or prompt before the server starts",
    not_connected="{client} is not connected: run the server with its lifespan, e.g. `async with Client(server)`",
)


def _noop() -> None: ...  # pragma: no cover


# The class of FastMCP's `Depends` markers
_DEPENDS: type[Any] = type(Depends(_noop))

# Where FastMCP takes the functions of a server: its decorators come here too
_REGISTRATIONS = ("add_tool", "add_resource", "add_prompt")

# The container that the lifespan of a set-up server connected, in the task that runs it: FastMCP enters the
# lifespan of a mounted server inside the lifespan of the server it is mounted on
_running: ContextVar[Dependencies | None] = ContextVar("nuke_di_fastmcp_running", default=None)


@dataclass(eq=False)
class _Server:
    """
    What `setup()` keeps of one server: its container, the clients of its functions, the servers mounted on it.
    """

    name: str
    container: Dependencies
    bindings: list[Binding] = field(default_factory=list)
    mounted: list[FastMCP[Any]] = field(default_factory=list)


_SERVERS: "weakref.WeakKeyDictionary[FastMCP[Any], _Server]" = weakref.WeakKeyDictionary()


def setup(server: FastMCP[Any], container: Dependencies = DI) -> None:
    """
    Fill client arguments of the tools, resources and prompts added to `server` from now on, and run `container`
    with the server: connect it when the server's lifespan starts, disconnect it when the lifespan ends. A server
    mounted on it after `setup()` and set up on the same container shares its clients.
    """
    if getattr(server._lifespan, "__nuke_di__", False):
        raise TypeError("setup() was already called for this app")
    state = _SERVERS[server] = _Server(server.name, container)
    # The lifespan FastMCP enters once for every transport and the in-memory `Client`; the server's own lifespan
    # runs inside, so it can use the clients
    server._lifespan = _lifespan(server._lifespan, state)

    provider = server.local_provider
    for name in _REGISTRATIONS:
        setattr(provider, name, _registration(getattr(provider, name), state))

    mount = server.mount

    def mount_server(child: FastMCP[Any], *args: Any, **kwargs: Any) -> None:
        state.mounted.append(child)
        mount(child, *args, **kwargs)

    server.mount = mount_server  # type: ignore[method-assign, assignment]


def _registration(add: Callable[[Any], Any], state: _Server) -> Callable[[Any], Any]:
    def register(component: Any) -> Any:
        # FastMCP reads the signature of a function when it is added; a Tool, Resource or Prompt object built
        # beforehand is passed as it is
        state.bindings += _bind(component, state.container)
        return add(component)

    return register


def _lifespan(original: Callable[..., Any], state: _Server) -> Callable[..., Any]:
    @asynccontextmanager
    async def lifespan(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        if _running.get() is state.container:
            # Mounted on a server that runs on the same container, which started the clients of this one too
            _check_started(state)
            async with original(*args, **kwargs) as result:
                yield result
            return
        token = _running.set(state.container)
        try:
            async with running(state.container, unique(_bindings(state))), original(*args, **kwargs) as result:
                yield result
        finally:
            _running.reset(token)

    lifespan.__nuke_di__ = True  # type: ignore[attr-defined]
    return lifespan


def _bindings(state: _Server) -> list[Binding]:
    """
    The clients of a server and of the servers mounted on it, at any depth, that share its container.
    """
    found = list(state.bindings)
    for child in state.mounted:
        mounted = _SERVERS.get(child)
        if mounted is not None and mounted.container is state.container:
            found += _bindings(mounted)
    return found


def _check_started(state: _Server) -> None:
    if any(binding.instance is None for binding in state.bindings):
        raise RuntimeError(
            f"nuke-di clients failed to start: {state.name} is mounted on a server that runs on the same container, "
            f"but not through it: call setup() of that server before mount()"
        )


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
    marks = vars(function)
    owner = marks.get("__nuke_di_owner__")
    if owner is not None and owner[1] is not _FASTMCP:
        # The kit's message, which it raises for the reverse order too
        raise TypeError(
            f"{sname(function)} takes clients in both {owner[1].name} and {_FASTMCP.name} handlers: nuke-di "
            f"rewrites its signature for one framework, so give each framework its own function"
        )
    if owner is not None:
        # Bound once, whatever the container: FastMCP caches a function's signature for good, so every server
        # that starts resolves the same bindings
        return marks["__nuke_di_bindings__"]  # type: ignore[no-any-return]
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
        if client is None or isinstance(param.default, Dependency):
            # A `Depends(...)`, `CurrentContext()` or other marker of the user's own stays
            kept.append(param)
            continue
        binding = Binding(client, container, _FASTMCP)
        found.append(binding)
        # Keyword-only: a parameter with a default cannot precede one without, and FastMCP passes every argument
        # by name
        clients.append(param.replace(kind=param.KEYWORD_ONLY, default=Depends(binding.get)))

    if not clients:
        # Nothing to rewrite here; the functions it depends on are bound once themselves
        return found
    if _read_by_fastmcp(function):
        raise TypeError(
            f"FastMCP read {sname(function)} before setup() and keeps the signature it read for good: call setup() "
            f"before the function is first added to any server, and start the process again"
        )
    # Sorted by kind, which keeps the order within each kind: the clients go after the arguments the caller
    # passes, before `**kwargs`
    parameters = sorted([*kept, *clients], key=lambda param: param.kind)
    function.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    function.__nuke_di_owner__ = (weakref.ref(container), _FASTMCP)  # type: ignore[attr-defined]
    function.__nuke_di_bindings__ = found  # type: ignore[attr-defined]
    return found


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
    target: Any
    if isinstance(call, functools.partial):
        target, shape = call.func, f"a functools.partial of {sname(call.func)}"
    elif inspect.isclass(call):
        target, shape = call.__init__, f"the class {sname(call)}"
    else:
        target, shape = getattr(type(call), "__call__", None), f"the callable object {sname(type(call))}"  # noqa: B004
    try:
        hints = get_type_hints(target, include_extras=True)
        parameters = inspect.signature(call).parameters
    except (NameError, TypeError, ValueError):
        return
    for name in parameters:
        client = client_of(hints.get(name))
        if client is not None:
            raise TypeError(
                f'Argument "{name}" of {shape} is {sname(client)}: nuke-di fills the clients of a function or a bound '
                f"method in FastMCP, so declare a function instead"
            )
