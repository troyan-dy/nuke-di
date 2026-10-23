import contextlib
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeAlias, TypeVar, cast, overload

from nuke_di._container import (
    Connect,
    already_connected,
    disconnect_all,
    inject_function,
    log_startup,
    not_a_client,
    resolve_locked,
    resolve_refused,
)
from nuke_di._container.replacements import (
    autospec,
    check_mockable,
    check_overridable,
    close_override,
    exit_override,
    register_replacement,
)
from nuke_di.graph import Graph, snapshot
from nuke_di.options import DependenciesSettings
from nuke_di.timings import ClientTiming
from nuke_di.types import ClientT, NotSingletonClient, isnotsingleton

ReturnT = TypeVar("ReturnT")

# The class asked for and the client passed, for one `__init__` argument; the class a Replacement stands in for and
# the Replacement
_Dependency: TypeAlias = tuple[type[NotSingletonClient], NotSingletonClient]


@dataclass(repr=False)
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

    clients: OrderedDict[type[NotSingletonClient], NotSingletonClient] = field(default_factory=OrderedDict)
    connect_clients: list[NotSingletonClient] = field(default_factory=list)
    settings: DependenciesSettings = field(default_factory=DependenciesSettings)
    connected: bool = field(default=False, init=False)
    # One entry per client of the last connect(), in resolution order: a client after its dependencies; kept after
    # disconnect()
    timings: list[ClientTiming] = field(default_factory=list, init=False)
    # The entries of `timings` by `id()` of their client, until the container is flushed
    _timings: dict[int, ClientTiming] = field(default_factory=dict, init=False)
    # The class asked for and the client passed, per `__init__` argument of every client in `connect_clients`, keyed
    # by `id()`: dataclass clients may be unhashable. Orders connect() and disconnect(), and makes `graph()`; the
    # class tells a Replacement apart from the clients it stands in for.
    _dependencies: dict[int, dict[str, _Dependency]] = field(default_factory=dict, init=False)
    # Replacements registered by `mock()` and `override()`, a subset of `clients`
    _replacements: dict[type[NotSingletonClient], NotSingletonClient] = field(default_factory=dict, init=False)
    # Replacements of the open `override()` blocks, outermost first; `flush()` keeps them
    _overrides: list[_Dependency] = field(default_factory=list, init=False)
    # Clients being resolved right now, the open frames of `resolve_tree()`, outermost first: finds cycles and names
    # the path in errors
    _resolving: list[type[NotSingletonClient]] = field(default_factory=list, init=False)
    # The same classes, for the cycle check: the list would be scanned once per client, as long as the depth
    _resolving_set: set[type[NotSingletonClient]] = field(default_factory=set, init=False)
    # The function `inject()` is resolving for, named first in the path but never part of a cycle
    _injecting: Callable[..., Any] | None = field(default=None, init=False)
    # Serializes the methods that build or drop clients across threads; reentrant, a client's `__init__` may resolve
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, compare=False)

    def __getstate__(self) -> dict[str, Any]:
        # A copy is another container, so it gets a lock of its own; an RLock cannot be copied or pickled anyway
        return {key: attribute for key, attribute in self.__dict__.items() if key != "_lock"}

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._lock = threading.RLock()

    async def __aenter__(self) -> None:
        await self.connect()

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    def flush(self) -> None:
        with self._lock:
            if self.connected:
                raise already_connected("flush()")

            self.clients = OrderedDict()
            self.connect_clients = []
            self._dependencies = {}
            self._timings = {}
            self._replacements = {}
            for client_class, replacement in self._overrides:
                register_replacement(self, client_class, replacement)

    async def connect(self) -> None:
        duration = await Connect(self).run()
        # Logged from here: the records name this module, `core`
        log_startup(self.timings, self.settings.connect_timeout, duration)

    async def disconnect(self) -> None:
        await disconnect_all(self)

    def resolve(self, client_class: type[ClientT]) -> ClientT:
        """
        Idempotent operation.
        """
        if self.connected:
            raise resolve_refused(client_class)

        # A resolved singleton is handed out without the lock: it is complete once it is in `clients`, and a lookup
        # in a dict is safe next to a writer on every build of CPython. The cold path takes the lock once for the
        # whole tree, so a tree of a thousand clients pays for one acquire, not a thousand.
        try:
            inst = self.clients.get(client_class)
        except TypeError:
            # An unhashable argument, `resolve({})`, is no client either: it gets the message below, not a bare
            # TypeError from the lookup
            inst = None
        if inst is not None:
            return cast(ClientT, inst)

        # A plain class would be built and appended, and the process would die at connect() with an
        # AttributeError on `connect`; a type checker sees the bound, `Any` and `# type: ignore` do not. Checked
        # after the lookup, which never finds a non-client since mock() refuses one too: the check costs about
        # 60 ns, a warm hit must not pay it
        if not isnotsingleton(client_class):
            raise not_a_client(client_class)

        with self._lock:
            # Checked again under the lock, where connect() flips it
            if self.connected:
                raise resolve_refused(client_class)
            return resolve_locked(self, client_class)

    def graph(self) -> Graph:
        """
        A snapshot of the resolved clients with their dependencies.

        `flush()` empties it, apart from the Replacements of the open `override()` blocks, which it keeps.
        """
        return snapshot(self._replacements, self.connect_clients, self._dependencies)

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
        if not isnotsingleton(client_class):
            raise not_a_client(client_class)
        with self._lock:
            current = check_mockable(self, client_class, new)
            if current is not None:
                return current

            replacement = autospec(client_class) if new is None else new
            register_replacement(self, client_class, replacement)
            return self._replacements[client_class]

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
            entry: _Dependency = (client_class, replacement)
            self._overrides.append(entry)
        try:
            yield replacement
        except BaseException:
            # The exception of the block matters more than the state of the container
            close_override(self, entry)
            raise

        exit_override(self, entry, name)


DI = Dependencies()
