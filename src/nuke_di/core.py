import contextlib
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, TypeVar, cast, overload

from nuke_di._container import (
    Connect,
    already_connected,
    disconnect_all,
    inject_function,
    not_a_client,
    resolve_locked,
    resolve_refused,
)
from nuke_di._container.registry import BindingLifetime, ClientClass, Registration
from nuke_di._container.replacements import (
    autospec,
    check_mockable,
    check_overridable,
    close_override,
    exit_override,
    register_replacement,
)
from nuke_di.naming import display_names
from nuke_di.options import DependenciesSettings
from nuke_di.types import Client, ClientT, isclient

ReturnT = TypeVar("ReturnT")

_Override: TypeAlias = tuple[ClientClass, Client]
_Lifecycle: TypeAlias = Literal["connecting", "connected", "disconnecting"]


@dataclass(eq=False, repr=False)
class Dependencies:  # noqa: WPS214
    """
    The container: resolves clients from type hints and drives their connect / disconnect lifecycle.

    `resolve()`, `inject()`, `mock()`, `override()` and `flush()` are safe to call from several threads: one lock per
    container serializes them, so a singleton asked for by two threads at once is built once and a resolve in one
    thread never sees the path of another as a cycle. The lock is reentrant, for a client whose `__init__` resolves
    from the same container in the same thread (an `__init__` that waits for another thread to resolve from the
    container deadlocks). It is not held inside an `override()` block, only while the block registers its
    Replacement and flushes on exit, so other threads resolve meanwhile and the exit flushes what they resolved.
    `connect()` and `disconnect()` are not locked: they belong to one event loop; they flip `connected` and take
    their snapshot of the clients under the lock, so a resolve in flight either completes before a connect() and is
    connected, or fails after it.
    """

    settings: DependenciesSettings | None = None
    _clients: dict[ClientClass, Registration] = field(default_factory=dict, init=False)
    _state: _Lifecycle | None = field(default=None, init=False)
    _binding_lifetime: BindingLifetime = field(default_factory=BindingLifetime, init=False)
    # Replacements of open override blocks, outermost first. They survive a flush.
    _overrides: list[_Override] = field(default_factory=list, init=False)
    # Clients being resolved right now, the open frames of `resolve_tree()`, outermost first: finds cycles and names
    # the path in errors
    _resolving: list[type[Client]] = field(default_factory=list, init=False)
    # The same classes, for the cycle check: the list would be scanned once per client, as long as the depth
    _resolving_set: set[type[Client]] = field(default_factory=set, init=False)
    # The function `inject()` is resolving for, named first in the path but never part of a cycle
    _injecting: Callable[..., Any] | None = field(default=None, init=False)
    # Serializes the methods that build or drop clients across threads; reentrant, a client's `__init__` may resolve
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def __repr__(self) -> str:
        with self._lock:
            names = display_names(self._clients)
            resolved = self._lifecycle_clients()
            clients = ", ".join(names[key] for key in resolved)
            replacements = ", ".join(names[key] for key in names if key not in resolved)
            return f"Dependencies(connected={self.connected}, clients=[{clients}], replacements=[{replacements}])"

    def __copy__(self) -> "Dependencies":
        raise TypeError("Dependencies cannot be copied; create a fresh container")

    async def __aenter__(self) -> None:
        await self.connect()

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    @property
    def clients(self) -> Mapping[ClientClass, Client]:
        """A read-only snapshot of resolved clients and replacements, in registration order."""
        with self._lock:
            return MappingProxyType({key: entry.client for key, entry in self._clients.items()})

    @property
    def connected(self) -> bool:
        """Whether the container is reserved by an active lifecycle, including startup and teardown."""
        return self._state is not None

    def flush(self) -> None:
        with self._lock:
            if self.connected:
                raise already_connected("flush()")

            self._reset()

    async def connect(self) -> None:
        await Connect(self).run()

    async def disconnect(self) -> None:
        await disconnect_all(self)

    def resolve(self, client_class: type[ClientT]) -> ClientT:
        """Return a registered client. Only an idle container can build a missing client."""
        state = self._state
        if state == "disconnecting":
            raise resolve_refused(client_class, state)

        # Read the lifecycle before looking up the cache. An overlapping reset can only replace the cache
        # with fresh registrations. A lookup that starts during teardown is refused above.
        try:
            entry = self._clients.get(client_class)
        except TypeError:
            entry = None
        if entry is not None:
            return cast(ClientT, entry.client)

        if state is not None:
            raise resolve_refused(client_class, state)
        if not isclient(client_class):
            raise not_a_client(client_class)

        with self._lock:
            return resolve_locked(self, client_class)

    def inject(self, func: Callable[..., ReturnT]) -> Callable[..., ReturnT]:
        """
        Bind the dependencies from the signature of `func`.

        note: `func` may be a function or a class. The result keeps the return type of `func`; its remaining
        arguments are typed by the mypy plugin of nuke_di only, a type checker cannot subtract the client arguments
        from a signature on its own.
        """
        with self._lock:
            return inject_function(self, func)

    # Typed like `unittest.mock.create_autospec`: an autospec mock is `Any`, so a test reaches its
    # `return_value` and `assert_awaited_once_with` under a strict type checker; a Replacement of your own
    # keeps its type
    @overload
    def mock(self, client_class: type[ClientT], new: None = None) -> Any: ...

    @overload
    def mock(self, client_class: type[ClientT], new: ClientT) -> ClientT: ...

    def mock(self, client_class: type[ClientT], new: ClientT | None = None) -> Any:
        """
        Register a Replacement for `client_class`, an autospec mock by default; `flush()` drops it.
        """
        if not isclient(client_class):
            raise not_a_client(client_class)
        with self._lock:
            current = check_mockable(self, client_class, new)
            if current is not None:
                return current

            replacement = autospec(client_class) if new is None else new
            register_replacement(self, client_class, replacement)
            return replacement

    @overload
    def override(self, client_class: type[ClientT], new: None = None) -> contextlib.AbstractContextManager[Any]: ...

    @overload
    def override(self, client_class: type[ClientT], new: ClientT) -> contextlib.AbstractContextManager[ClientT]: ...

    @contextlib.contextmanager
    def override(self, client_class: type[ClientT], new: ClientT | None = None) -> Iterator[Any]:
        """
        Register a Replacement for `client_class` that lasts until the end of the block, `flush()` included.

        The container must have no resolved clients on entry and is flushed on exit,
        so nothing resolved with the Replacement outlives the block.
        """
        # The lock covers the registration only: the block runs the caller's code, which resolves on its own
        with self._lock:
            name = check_overridable(self, client_class)
            replacement = self.mock(client_class, new)
            entry: _Override = (client_class, replacement)
            self._overrides.append(entry)
        try:
            yield replacement
        except BaseException:
            # The exception of the block matters more than the state of the container
            close_override(self, entry)
            raise

        exit_override(self, entry, name)

    def _reset(self) -> None:
        with self._lock:
            self._binding_lifetime.valid = False
            self._binding_lifetime = BindingLifetime()
            self._clients = {key: Registration(client, None) for key, client in self._overrides}
            self._state = None

    def _lifecycle_clients(self) -> dict[ClientClass, Client]:
        entries = self._clients.items()
        return {key: entry.client for key, entry in entries if entry.dependencies is not None}

    def _settings(self) -> DependenciesSettings:
        # Called under the lock when reserving the lifecycle. A failed read leaves settings unset.
        if self.settings is None:
            self.settings = DependenciesSettings()
        return self.settings


DI = Dependencies()
