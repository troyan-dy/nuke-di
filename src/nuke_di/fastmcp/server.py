"""
setup() of a server: the functions added to it take clients, and the container runs with its lifespan, shared
with the servers mounted on it.
"""

import weakref
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from fastmcp import FastMCP

from nuke_di.core import DI, Dependencies
from nuke_di.fastmcp.signatures import _bind
from nuke_di.integration import Binding, running, unique

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
    state = _Server(server.name, container)
    _SERVERS[server] = state
    # The lifespan FastMCP enters once for every transport and the in-memory `Client`; the server's own lifespan
    # runs inside, so it can use the clients
    server._lifespan = _lifespan(server._lifespan, state)

    provider = server.local_provider
    for name in _REGISTRATIONS:
        setattr(provider, name, _registration(getattr(provider, name), state))
    server.mount = partial(_mount, server.mount, state)  # type: ignore[method-assign]


def _registration(
    add: Callable[[Any], Any],
    state: _Server,
) -> Callable[[Any], Any]:
    def wrapper(component: Any) -> Any:
        # FastMCP reads the signature of a function when it is added; a Tool, Resource or Prompt object built
        # beforehand is passed as it is
        state.bindings.extend(_bind(component, state.container))
        return add(component)

    return wrapper


def _mount(
    mount: Callable[..., None],
    state: _Server,
    child: FastMCP[Any],
    *args: Any,
    **kwargs: Any,
) -> None:
    state.mounted.append(child)
    mount(child, *args, **kwargs)


def _lifespan(original: Callable[..., Any], state: _Server) -> Callable[..., Any]:
    @asynccontextmanager
    async def wrapper(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        if _running.get() is state.container:
            # Mounted on a server that runs on the same container, which started the clients of this one too
            _check_started(state)
            async with original(*args, **kwargs) as lifespan_state:
                yield lifespan_state
            return
        bindings = unique(_bindings(state))
        token = _running.set(state.container)
        try:
            async with running(state.container, bindings), original(*args, **kwargs) as lifespan_state:
                yield lifespan_state
        finally:
            _running.reset(token)

    wrapper.__nuke_di__ = True  # type: ignore[attr-defined]
    return wrapper


def _bindings(state: _Server) -> list[Binding]:
    """
    The clients of a server and of the servers mounted on it, at any depth, that share its container.
    """
    found = list(state.bindings)
    for child in state.mounted:
        mounted = _SERVERS.get(child)
        if mounted is not None and mounted.container is state.container:
            found.extend(_bindings(mounted))
    return found


def _check_started(state: _Server) -> None:
    if any(binding.instance is None for binding in state.bindings):
        raise RuntimeError(
            f"nuke-di clients failed to start: {state.name} is mounted on a server that runs on the same container, "
            f"but not through it: call setup() of that server before mount()"
        )
