import asyncio
import contextlib
import inspect
import logging
import threading
import time
import types
from collections import Counter, OrderedDict
from collections.abc import Callable, Coroutine, Iterable, Iterator
from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType
from typing import Any, Literal, NamedTuple, TypeVar, Union, cast, get_args, get_origin, get_type_hints, overload

from nuke_di.errors import (
    CircularDependencyError,
    ConnectError,
    ConnectTimeoutError,
    InitializeDependencyError,
    InvalidSignatureError,
)
from nuke_di.graph import Graph, Node
from nuke_di.logs import fields
from nuke_di.options import DependenciesSettings
from nuke_di.timings import ClientTiming, Outcome
from nuke_di.types import Client, NotSingletonClient
from nuke_di.utils import isa, qualname, sname

logger = logging.getLogger(__name__)
isclient = isa(Client)
isnotsingleton = isa(NotSingletonClient)

CT = TypeVar("CT", bound=NotSingletonClient)
R = TypeVar("R")

Limiter = asyncio.Semaphore | contextlib.nullcontext[None]

# `*args` and `**kwargs`: never filled by the container
_VARIADIC = frozenset({inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD})


class _ClientConnectError(Exception):
    """
    Carries a `ConnectError` out of a connect task.

    `ConnectError` is a `SystemExit`, and a `SystemExit` raised inside a task escapes the event loop.
    """

    def __init__(self, error: ConnectError) -> None:
        super().__init__(str(error))
        self.error = error


@dataclass(repr=False)
class Dependencies:
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
    _dependencies: dict[int, dict[str, tuple[type[NotSingletonClient], NotSingletonClient]]] = field(
        default_factory=dict, init=False
    )
    # Replacements registered by `mock()` and `override()`, a subset of `clients`
    _replacements: dict[type[NotSingletonClient], NotSingletonClient] = field(default_factory=dict, init=False)
    # Replacements of the open `override()` blocks, outermost first; `flush()` keeps them
    _overrides: list[tuple[type[NotSingletonClient], NotSingletonClient]] = field(default_factory=list, init=False)
    # Clients being resolved right now, the open frames of `_resolve_tree()`, outermost first: finds cycles and names
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
        return {key: value for key, value in vars(self).items() if key != "_lock"}

    def __setstate__(self, state: dict[str, Any]) -> None:
        vars(self).update(state)
        self._lock = threading.RLock()

    async def __aenter__(self) -> None:
        await self.connect()

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    def flush(self) -> None:
        with self._lock:
            if self.connected is True:
                raise ConnectError("flush(): already connected, call disconnect() first")

            self.clients = OrderedDict()
            self.connect_clients = []
            self._dependencies = {}
            self._timings = {}
            self._replacements = {}
            for cls, replacement in self._overrides:
                self._register_replacement(cls, replacement)

    def _abandon(self) -> None:
        """
        Forget every client without disconnecting it, for a caller that has no event loop left to do it.
        """
        self.connected = False
        self.flush()

    async def connect(self) -> None:
        with self._lock:
            if self.connected is True:
                raise ConnectError("connect(): already connected, call disconnect() first")

            # Flipped and snapshotted together: a thread inside resolve() finishes before or fails after, never
            # leaves a client that this connect() does not see
            self.connected = True
            clients = list(self.connect_clients)
        limiter = self._limiter()
        # Computed once: the timings, the logs and the errors of this connect() all name a client the same way
        names = self._names()
        self._timings = {id(client): ClientTiming(names[type(client)]) for client in clients}
        self.timings = list(self._timings.values())
        # Clients whose connect() finished, so a failure knows what to roll back
        connected: list[NotSingletonClient] = []
        started = time.perf_counter()

        try:
            # A client connects once its dependencies have; the first failure cancels every client still connecting
            # and every client still waiting
            await _in_order(
                clients,
                self._dependencies_among(clients),
                partial(self._connect_client, limiter=limiter, connected=connected, total=len(clients)),
            )

        except ExceptionGroup as eg:
            await self._rollback(connected)
            # Every failure is logged already, the first one stops the application
            error = cast(_ClientConnectError, eg.exceptions[0]).error
            raise error from error.__cause__

        except asyncio.CancelledError:
            await self._rollback(connected)
            raise

        self._log_startup(time.perf_counter() - started)

    def _log_startup(self, duration: float) -> None:
        if not self.timings:
            return

        slowest = sorted(self.timings, key=lambda timing: timing.connect or 0, reverse=True)[:3]
        logger.info(
            "Connected %s in %.2fs (slowest: %s)",
            _count(len(self.timings), "client"),
            duration,
            ", ".join(f"{timing.name} {timing.connect or 0:.2f}s" for timing in slowest),
            extra=fields(duration=duration),
        )

        timeout = self.settings.connect_timeout
        for timing in self.timings:
            if timing.connect is not None and timing.connect > timeout / 2:
                logger.warning(
                    "Client %s took %.2fs to connect, more than half of CONNECT_TIMEOUT_SECONDS (%gs)",
                    timing.name,
                    timing.connect,
                    timeout,
                    extra=_fields(timing, timing.connect),
                )

    async def _connect_client(
        self, client: NotSingletonClient, *, limiter: Limiter, connected: list[NotSingletonClient], total: int
    ) -> None:
        timing = self._timings[id(client)]
        name = timing.name
        # The record's fields are built per client: skipped when nobody listens
        debug = logger.isEnabledFor(logging.DEBUG)
        try:
            async with limiter:
                if debug:
                    logger.debug(
                        "Connecting client %s (%d/%d connected)", name, len(connected), total, extra=_fields(timing)
                    )
                with _measure(timing, "connect"):
                    await _within(self.settings.connect_timeout, client.connect())
            connected.append(client)
            if debug:
                logger.debug(
                    "Connected client %s in %.3fs (%d/%d connected)",
                    name,
                    timing.connect,
                    len(connected),
                    total,
                    extra=_fields(timing, timing.connect),
                )

        except TimeoutError as exc:
            timeout = self.settings.connect_timeout
            logger.exception(
                "%s did not connect within %gs (CONNECT_TIMEOUT_SECONDS)",
                name,
                timeout,
                extra=_fields(timing, timing.connect),
            )
            error: ConnectError = ConnectTimeoutError(
                f"{name} did not connect within {timeout:g}s (CONNECT_TIMEOUT_SECONDS)"
            )
            error.__cause__ = exc
            raise _ClientConnectError(error) from exc

        except Exception as exc:
            logger.exception(
                "%s.connect() raised %s: %s", name, type(exc).__name__, exc, extra=_fields(timing, timing.connect)
            )
            error = ConnectError(f"{name}.connect() raised {type(exc).__name__}: {exc}")
            error.__cause__ = exc
            raise _ClientConnectError(error) from exc

    async def disconnect(self) -> None:
        if self.connected is False:
            raise ConnectError("disconnect(): already disconnected")

        await self._disconnect_clients(self.connect_clients)

    async def _rollback(self, connected: list[NotSingletonClient]) -> None:
        logger.debug("Connect failed, disconnecting %d connected clients", len(connected), extra=fields())
        await self._disconnect_clients(connected)

    async def _disconnect_clients(self, clients: list[NotSingletonClient]) -> None:
        with self._lock:
            self.connected = False
            clients = list(clients)
        # A client disconnects once its consumers have, however their disconnect() ended
        consumers: dict[int, list[int]] = {id(client): [] for client in clients}
        for consumer, dependencies in self._dependencies_among(clients).items():
            for dependency in dependencies:
                consumers[dependency].append(consumer)
        # Clients whose disconnect() ended, for the progress in the records
        disconnected: list[NotSingletonClient] = []

        try:
            # Only a cancellation of disconnect() itself ends it early: _disconnect_client logs and swallows what a
            # client raises
            await _in_order(
                clients,
                consumers,
                partial(
                    self._disconnect_client, limiter=self._limiter(), disconnected=disconnected, total=len(clients)
                ),
            )
        finally:
            # Cancelled or not, the container keeps no half-disconnected client for the next connect() to reuse
            self.flush()

    async def _disconnect_client(
        self, client: NotSingletonClient, *, limiter: Limiter, disconnected: list[NotSingletonClient], total: int
    ) -> None:
        timing = self._timings[id(client)]
        name = timing.name
        debug = logger.isEnabledFor(logging.DEBUG)
        try:
            async with limiter:
                if debug:
                    logger.debug(
                        "Disconnecting client %s (%d/%d disconnected)",
                        name,
                        len(disconnected),
                        total,
                        extra=_fields(timing),
                    )
                try:
                    with _measure(timing, "disconnect"):
                        await _within(self.settings.disconnect_timeout, client.disconnect())
                finally:
                    disconnected.append(client)
            if debug:
                logger.debug(
                    "Disconnected client %s in %.3fs (%d/%d disconnected)",
                    name,
                    timing.disconnect,
                    len(disconnected),
                    total,
                    extra=_fields(timing, timing.disconnect),
                )
        except asyncio.CancelledError:
            # A CancelledError of the client's own, re-raised from a task it awaited, ends its disconnect() alone: its
            # dependencies still have to be stopped. A cancellation of disconnect() itself goes on.
            task = asyncio.current_task()
            if task is None or task.cancelling():
                raise
        except TimeoutError:
            logger.exception(
                "%s did not disconnect within %gs (DISCONNECT_TIMEOUT_SECONDS)",
                name,
                self.settings.disconnect_timeout,
                extra=_fields(timing, timing.disconnect),
            )
        except Exception as exc:
            # The client failed, but the rest still have to be stopped
            logger.exception(
                "%s.disconnect() raised %s: %s",
                name,
                type(exc).__name__,
                exc,
                extra=_fields(timing, timing.disconnect),
            )

    def _limiter(self) -> Limiter:
        if self.settings.connect_concurrency == 0:
            return contextlib.nullcontext()
        return asyncio.Semaphore(self.settings.connect_concurrency)

    def _dependencies_among(self, clients: list[NotSingletonClient]) -> dict[int, list[int]]:
        """
        The dependencies of every client of `clients` that are among `clients`, by `id()`: a Replacement is never
        connected, so nothing waits for it.
        """
        ids = {id(client) for client in clients}
        return {
            id(client): [
                id(dependency)
                for _, dependency in self._dependencies.get(id(client), {}).values()
                if id(dependency) in ids
            ]
            for client in clients
        }

    def resolve(self, cls: type[CT]) -> CT:
        """
        Idempotent operation.
        """
        if self.connected is True:
            # `_type_name`, not `qualname`: `cls` may be no class at all
            raise self._state_error(f"resolve({_type_name(cls)})")

        # A resolved singleton is handed out without the lock: it is complete once it is in `clients`, and a lookup
        # in a dict is safe next to a writer on every build of CPython. The cold path takes the lock once for the
        # whole tree, so a tree of a thousand clients pays for one acquire, not a thousand.
        try:
            inst = self.clients.get(cls)
        except TypeError:
            # An unhashable argument, `resolve({})`, is no client either: it gets the message below, not a bare
            # TypeError from the lookup
            inst = None
        if inst is not None:
            return cast(CT, inst)

        # A plain class would be built and appended, and the process would die at connect() with an
        # AttributeError on `connect`; a type checker sees the bound, `Any` and `# type: ignore` do not. Checked
        # after the lookup, which never finds a non-client since mock() refuses one too: the check costs about
        # 60 ns, a warm hit must not pay it
        if not isnotsingleton(cls):
            raise _not_a_client(cls)

        with self._lock:
            # Checked again under the lock, where connect() flips it
            if self.connected is True:
                raise self._state_error(f"resolve({_type_name(cls)})")
            return self._resolve(cls)

    def _resolve(self, cls: type[CT]) -> CT:
        """
        Build `cls` with the lock held: the caller holds it for the whole tree.
        """
        # Checked again under the lock: another thread may have built it while this one waited
        inst = self.clients.get(cls)
        if inst is not None:
            return cast(CT, inst)

        # Not always 0: a client's `__init__` that resolves on its own runs inside its consumer's frame, and only the
        # frames opened from here are this call's to close
        opened = len(self._resolving)
        try:
            return cast(CT, self._resolve_tree(cls))
        except BaseException:
            # The frames a failure leaves open are off the path, as a recursion's `finally` would leave them: the next
            # resolve sees no stale cycle and names a path of its own
            self._resolving_set.difference_update(self._resolving[opened:])
            del self._resolving[opened:]
            raise

    def _resolve_tree(self, cls: type[NotSingletonClient]) -> NotSingletonClient:
        """
        Build `cls` and its dependencies, each before its consumer, on a stack of frames instead of the call stack.

        A call per client of a chain would stop it at the recursion limit, a few hundred clients long; the frames grow
        with the chain. A frame is a client whose arguments are being resolved, and `_resolving` is the class of every
        open frame, outermost first, so the cycle check and the path in errors are what a recursion had.
        """
        resolving, resolving_set = self._resolving, self._resolving_set
        # The open frames but the innermost, outermost first, each waiting for the client of its argument `key`
        waiting: list[_Frame] = []
        while True:
            # Open a frame for `cls`: on the path from here on, so a cycle back to it and a signature error name it
            if cls in resolving_set:
                raise CircularDependencyError(f"Circular dependency: {self._path(cls)}")
            if logger.isEnabledFor(logging.DEBUG):
                name = qualname(cls)
                logger.debug('Resolving dependency "%s"', name, extra=fields(client=name))
            resolving.append(cls)
            resolving_set.add(cls)
            arguments = self._client_arguments(cls)
            init: dict[str, NotSingletonClient] = {}
            # Most clients of a tree take no client, and share one exhausted cursor
            pending = iter(arguments.items()) if arguments else _NO_ARGUMENTS

            while True:
                # The innermost frame takes the clients that exist; a break leaves `dep` to build first
                for key, dep in pending:
                    # Not kept in a local: an `__init__` that flushes the container replaces the dict
                    inst = self.clients.get(dep)
                    if inst is None:
                        break
                    init[key] = inst
                else:
                    # Every argument is in `init`: the client is built and its frame closed. Off the path before
                    # `__init__`, so an `__init__` that resolves on its own sees the path of its consumers.
                    resolving.pop()
                    resolving_set.remove(cls)
                    try:
                        inst = cls(**init)
                    except Exception as exc:
                        raise self._init_error(cls, exc) from exc

                    self._dependencies[id(inst)] = {arg: (arguments[arg], client) for arg, client in init.items()}
                    self.connect_clients.append(inst)
                    # Published last: the lock-free lookup of resolve() hands out a singleton that the container
                    # accounts for
                    if isclient(cls):
                        self.clients[cls] = inst

                    if not waiting:
                        return inst
                    # The consumer resumes at its next argument
                    cls, arguments, init, pending, key = waiting.pop()
                    init[key] = inst
                    continue
                # Before the arguments after it, which is the order of a recursion
                waiting.append((cls, arguments, init, pending, key))
                cls = dep
                break

    def _init_error(self, cls: type[NotSingletonClient], exc: Exception) -> InitializeDependencyError:
        """
        The error of an `__init__` that raised `exc`, logged with the path that led to it.
        """
        # `cls` left the path when its arguments were resolved, so it is named at the end of it here
        name = self._name(cls)
        # The path before the cause: a cause such as a pydantic error spans lines
        where = f"{name}.__init__ raised {type(exc).__name__}{self._path_suffix(cls)}"
        logger.exception("%s: %s", where, exc, extra=fields(client=name))
        return InitializeDependencyError(f"{where}: {exc}")

    def graph(self) -> Graph:
        """
        A snapshot of the resolved clients with their dependencies.

        `flush()` empties it, apart from the Replacements of the open `override()` blocks, which it keeps.
        """
        # A Replacement is one object for every class it stands in for, and may be a resolved client too,
        # so its nodes are keyed by class and the resolved clients by `id()`
        replaced = {
            cls: Node(cls=cls, singleton=isclient(cls), replacement=replacement)
            for cls, replacement in self._replacements.items()
        }
        resolved: dict[int, Node] = {}
        # Resolution order: a client's dependencies have a node before the client does
        for client in self.connect_clients:
            dependencies = {
                key: replaced[cls] if self._replacements.get(cls) is dep else resolved[id(dep)]
                for key, (cls, dep) in self._dependencies.get(id(client), {}).items()
            }
            resolved[id(client)] = Node(
                cls=type(client),
                singleton=isclient(type(client)),
                replacement=None,
                dependencies=MappingProxyType(dependencies),
            )
        return Graph((*replaced.values(), *resolved.values()))

    def _client_arguments(self, cls: type[NotSingletonClient]) -> dict[str, type[NotSingletonClient]]:
        """
        The arguments of `cls.__init__` to fill with clients; fail on any other required argument.

        Read once per class in the process: the result depends on `__init__` alone, so it is kept on the class
        beside the `__init__` it was read from, and read again when the class gets another `__init__`.
        """
        init = cls.__init__
        if init is object.__init__:
            # `inspect.signature` parses the text signature of a slot wrapper on every call
            return {}

        # A subclass that inherits `__init__` finds its base class's entry and shares it; one that redefines
        # `__init__` fails the check below and stores its own
        entry: tuple[Callable[..., Any], dict[str, type[NotSingletonClient]]] | None = getattr(
            cls, "__nuke_di_arguments__", None
        )
        if entry is not None and entry[0] is init:
            return entry[1]

        # Only a success is kept: a type hint that fails to evaluate must fail on the next resolve too
        arguments = self._read_arguments(cls, init)
        try:
            cls.__nuke_di_arguments__ = (init, arguments)  # type: ignore[attr-defined]
        except AttributeError:
            # A metaclass that forbids setting attributes: the class is read again on the next resolve
            pass
        return arguments

    def _read_arguments(
        self, cls: type[NotSingletonClient], init: Callable[..., Any]
    ) -> dict[str, type[NotSingletonClient]]:
        try:
            hints = get_type_hints(init)
        except NameError as exc:
            raise self._hints_error(f"{self._name(cls)}.__init__", exc) from exc

        clients: dict[str, type[NotSingletonClient]] = {}
        for argument in _init_arguments(init):
            name = argument.name
            hint: Any = hints.get(name)
            if isnotsingleton(hint):
                if argument.positional_only:
                    raise self._signature_error(cls, name, "is positional-only, a client is passed by keyword")
                clients[name] = hint
            elif argument.has_default:
                # Not ours to fill: the default stays
                continue
            elif hint is None:
                raise self._signature_error(cls, name, "has no type hint")
            elif (client := _optional_client(hint)) is not None:
                raise self._signature_error(cls, name, f"is {qualname(client)} | None, a client cannot be optional")
            else:
                raise self._signature_error(cls, name, f"is {_type_name(hint)}, which is not a client")
        return clients

    def _hints_error(self, name: str, exc: NameError) -> InvalidSignatureError:
        return InvalidSignatureError(
            f'Cannot evaluate the type hints of "{name}": {exc}; a type hint must name something the module '
            f"defines or imports at runtime, not a class local to a function or imported under TYPE_CHECKING"
            f"{self._path_suffix()}"
        )

    def _signature_error(self, cls: type[NotSingletonClient], name: str, reason: str) -> InvalidSignatureError:
        return InvalidSignatureError(f'Argument "{name}" of "{self._name(cls)}.__init__" {reason}{self._path_suffix()}')

    def _state_error(self, call: str) -> ConnectError:
        """
        `call` builds the tree, and the container is connected: the message names the call, the state and the way out.
        """
        return ConnectError(
            f"{call}: the container is already connected; resolve, inject, mock and override only work before "
            f"connect(), flush() after disconnect()"
        )

    def _names(self, *more: type) -> dict[type, str]:
        """
        The display name of every class the container holds or is resolving, and of `more`.

        Built on demand, for an error or a Replacement: the clients are scanned once per call, which a resolve()
        of every client cannot afford.
        """
        return _display_names(
            {*self.clients, *(type(client) for client in self.connect_clients), *self._resolving, *more}
        )

    def _name(self, cls: type) -> str:
        return self._names(cls)[cls]

    def _path(self, *more: type[NotSingletonClient]) -> str:
        classes = [*self._resolving, *more]
        names = self._names(*classes)
        root = [] if self._injecting is None else [sname(self._injecting)]
        return " -> ".join([*root, *(names[cls] for cls in classes)])

    def _path_suffix(self, *more: type[NotSingletonClient]) -> str:
        # A root alone is no path: `more` is the client that left `_resolving` before it was built
        if self._injecting is None and not self._resolving:
            return ""
        return f" (resolving {self._path(*more)})"

    def inject(self, func: Callable[..., R]) -> Callable[..., R]:
        """
        Bind the dependencies from the signature of `func`.

        note: `func` may be a function or a class. The result keeps the return type of `func`; its remaining
        arguments are not typed, a type checker cannot subtract the client arguments from a signature.
        """
        with self._lock:
            if self.connected is True:
                raise self._state_error(f"inject({sname(func)})")
            signature = self._inspect(func)
        return partial(func, **signature)

    # Typed like `unittest.mock.create_autospec`: an autospec mock is `Any`, so a test reaches its
    # `return_value` and `assert_awaited_once_with` under a strict type checker; a Replacement of your own
    # keeps its type
    @overload
    def mock(self, cls: type[CT], new: None = None) -> Any: ...

    @overload
    def mock(self, cls: type[CT], new: CT) -> CT: ...

    def mock(self, cls: type[CT], new: CT | None = None) -> Any:
        """
        Register a Replacement for `cls`, an autospec mock by default; `flush()` drops it.
        """
        if not isnotsingleton(cls):
            raise _not_a_client(cls)
        with self._lock:
            name = self._name(cls)
            if self.connected is True:
                raise self._state_error(f"mock({name})")

            current = self._replacements.get(cls)
            if current is not None:
                if new is not None and new is not current:
                    raise ConnectError(f"{name} already has a replacement")
                return current
            # Consumers resolved before would keep the real client while the caller holds the Replacement
            if self._is_resolved(cls):
                raise ConnectError(f"{name} is already resolved, call mock() before resolve() or inject()")

            if new is None:
                # unittest costs every process a few milliseconds at import, and only tests mock
                from unittest.mock import create_autospec

                # A Replacement stands in for an instance: calling it is a TypeError, not another mock
                replacement = create_autospec(cls, instance=True)
            else:
                replacement = new
            self._register_replacement(cls, replacement)
            return self._replacements[cls]

    @overload
    def override(self, cls: type[CT], new: None = None) -> contextlib.AbstractContextManager[Any]: ...

    @overload
    def override(self, cls: type[CT], new: CT) -> contextlib.AbstractContextManager[CT]: ...

    @contextlib.contextmanager
    def override(self, cls: type[CT], new: CT | None = None) -> Iterator[Any]:
        """
        Register a Replacement for `cls` that lasts until the end of the block, `flush()` included.

        The container must have no resolved clients on entry and is flushed on exit,
        so nothing resolved with the Replacement outlives the block.
        """
        # The lock covers the registration only: the block runs the caller's code, which resolves on its own
        with self._lock:
            name = self._name(cls)
            if self.connected is True:
                raise self._state_error(f"override({name})")
            if self.connect_clients:
                # Flushing them on exit would silently drop what the caller resolved before the block
                names = self._names()
                resolved = ", ".join(sorted(names[type(client)] for client in self.connect_clients))
                raise ConnectError(f"override({name}) needs a container without resolved clients, found: {resolved}")
            if cls in self._replacements:
                raise ConnectError(f"{name} already has a replacement")

            replacement = self.mock(cls, new)
            entry: tuple[type[NotSingletonClient], NotSingletonClient] = (cls, replacement)
            self._overrides.append(entry)
        try:
            yield replacement
        except BaseException:
            # The exception of the block matters more than the state of the container
            self._close_override(entry)
            raise

        self._close_override(entry)
        if self.connected is True:
            raise ConnectError(f"override({name}) exited while the container is connected")

    def _close_override(self, entry: tuple[type[NotSingletonClient], NotSingletonClient]) -> None:
        with self._lock:
            self._overrides = [other for other in self._overrides if other is not entry]
            # A connected container is flushed by its disconnect()
            if self.connected is False:
                self.flush()

    def _is_resolved(self, cls: type[NotSingletonClient]) -> bool:
        # A NotSingletonClient is never cached in `clients`, only its instances are in `connect_clients`
        return cls in self.clients or any(type(client) is cls for client in self.connect_clients)

    def _register_replacement(self, cls: type[NotSingletonClient], replacement: NotSingletonClient) -> None:
        self.clients[cls] = replacement
        self._replacements[cls] = replacement

    def _inspect(self, func: Callable) -> dict[str, NotSingletonClient]:
        logger.debug('Parsing signature of func "%s"', sname(func), extra=fields())
        signature: dict[str, NotSingletonClient] = {}

        # get_type_hints silently skips unannotated arguments, so they are looked up in the signature
        for param in inspect.signature(func).parameters.values():
            if param.kind in _VARIADIC:
                continue
            if param.annotation is inspect.Parameter.empty:
                raise InvalidSignatureError(f'Argument "{param.name}" of "{sname(func)}" has no type hint')

        try:
            sig: dict[str, Any] = get_type_hints(func)
        except NameError as exc:
            raise self._hints_error(sname(func), exc) from exc
        sig.pop("return", None)

        # A client may inject() on its own while this one resolves, so the outer root is restored after it
        outer, self._injecting = self._injecting, func
        try:
            for key, value in sig.items():
                if isnotsingleton(value):
                    # `inject()` holds the lock for the whole signature
                    signature[key] = self._resolve(value)
        finally:
            self._injecting = outer

        return signature


# A client waiting for a dependency in `_resolve_tree()`: its class, the arguments of its `__init__` to fill with
# clients, the clients found so far, the arguments left (an iterator: the index of the next one) and the argument the
# dependency fills. A plain tuple, for speed: on 3.14 a NamedTuple cost a chain 10-13% per client and methods that
# open and close a frame 8-10%, measured.
_Frame = tuple[
    type[NotSingletonClient],
    dict[str, type[NotSingletonClient]],
    dict[str, NotSingletonClient],
    Iterator[tuple[str, type[NotSingletonClient]]],
    str,
]
# Exhausted: the cursor of every client without arguments
_NO_ARGUMENTS: Iterator[tuple[str, type[NotSingletonClient]]] = iter(())


class _Argument(NamedTuple):
    """
    One argument of an `__init__`, as much of its signature as the container needs.
    """

    name: str
    positional_only: bool
    has_default: bool


def _init_arguments(init: Callable[..., Any]) -> list[_Argument]:
    """
    The arguments of `init` after `self`, without `*args` and `**kwargs`.
    """
    # A plain function is read from its code object, which is what `inspect.signature` does at thirty times the cost;
    # a decorated one (`__wrapped__`), a declared signature or a C function go through `inspect.signature`
    if inspect.isfunction(init) and "__wrapped__" not in init.__dict__ and "__signature__" not in init.__dict__:
        code = init.__code__
        positional = code.co_argcount
        names = code.co_varnames[: positional + code.co_kwonlyargcount]
        positional_only = code.co_posonlyargcount
        first_default = positional - len(init.__defaults__ or ())
        kwdefaults = init.__kwdefaults__ or {}
        # `self` is the first argument, unless the method takes it through `*args`
        start = 1 if positional or not code.co_flags & inspect.CO_VARARGS else 0
        return [
            _Argument(
                name, index < positional_only, index >= first_default if index < positional else name in kwdefaults
            )
            for index, name in enumerate(names[start:], start)
        ]

    parameters = list(inspect.signature(init).parameters.values())[1:]
    return [
        _Argument(param.name, param.kind is param.POSITIONAL_ONLY, param.default is not param.empty)
        for param in parameters
        if param.kind not in _VARIADIC
    ]


def _fields(timing: ClientTiming, duration: float | None = None) -> dict[str, Any]:
    """
    The structured fields of a log record about one client.
    """
    return fields(client=timing.name, duration=duration)


@contextlib.contextmanager
def _measure(timing: ClientTiming, phase: Literal["connect", "disconnect"]) -> Iterator[None]:
    """
    Record how long the block took and how it ended as the `phase` of `timing`.
    """
    started = time.perf_counter()
    outcome: Outcome = "failed"
    try:
        yield
        outcome = "ok"
    except TimeoutError:
        outcome = "timed_out"
        raise
    except asyncio.CancelledError:
        # Another client failed, or the whole connect or disconnect was cancelled
        outcome = "cancelled"
        raise
    finally:
        setattr(timing, phase, time.perf_counter() - started)
        setattr(timing, f"{phase}_outcome", outcome)


async def _within(seconds: float, coro: Coroutine[Any, Any, None]) -> None:
    """
    Await a client's coroutine with a timeout, in the task of the caller.

    `asyncio.wait_for()` runs the coroutine in a task of its own on 3.11, about 40 µs per call, and in the caller's
    task only from 3.12. A timeout of zero or less expires before the coroutine starts, as `wait_for()` does;
    `asyncio.timeout(0)` alone would let it run up to its first suspension.
    """
    if seconds <= 0:
        coro.close()
        raise TimeoutError
    async with asyncio.timeout(seconds):
        await coro


async def _in_order(
    clients: list[NotSingletonClient],
    waits_for: dict[int, list[int]],
    step: Callable[[NotSingletonClient], Coroutine[Any, Any, None]],
) -> None:
    """
    Run `step` for every client of `clients` concurrently, each once `step` has returned for the clients it
    `waits_for`, by `id()`.

    A client that `step` raised for releases no one: in a connect, the consumers of a failed client must not start
    before the group cancels them. The first exception cancels every step still running or waiting.
    """
    done = {id(client): asyncio.Event() for client in clients}

    async def run(client: NotSingletonClient) -> None:
        for other in waits_for[id(client)]:
            await done[other].wait()
        await step(client)
        done[id(client)].set()

    async with asyncio.TaskGroup() as group:
        for client in clients:
            group.create_task(run(client))


def _display_names(classes: Iterable[type]) -> dict[type, str]:
    """
    How errors, logs and timings name every class of `classes`: by `qualname()`, and in full, module and
    `__qualname__` (`app.orders.Database`, `tests.test_x.test_one.<locals>.Database`), for the classes that share
    one, so two wrappers called `Database` are told apart. A unique name stays short.
    """
    names = {cls: qualname(cls) for cls in classes}
    shared = {name for name, count in Counter(names.values()).items() if count > 1}
    return {cls: f"{cls.__module__}.{cls.__qualname__}" if name in shared else name for cls, name in names.items()}


def _not_a_client(cls: Any) -> InvalidSignatureError:
    return InvalidSignatureError(f"{_type_name(cls)} is not a client: subclass Client or NotSingletonClient")


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _optional_client(hint: Any) -> type[NotSingletonClient] | None:
    if get_origin(hint) not in {Union, types.UnionType}:
        return None
    args = get_args(hint)
    clients = [arg for arg in args if isnotsingleton(arg)]
    return clients[0] if type(None) in args and clients else None


def _type_name(hint: Any) -> str:
    """
    A type hint as written in code: `int`, `list[int]`, `Database | int`, without module prefixes.
    """
    if hint is type(None):
        return "None"
    if hint is Ellipsis:
        return "..."
    if isinstance(hint, list):
        # The parameters of a Callable
        return f"[{', '.join(map(_type_name, hint))}]"
    origin = get_origin(hint)
    if origin in {Union, types.UnionType}:
        return " | ".join(map(_type_name, get_args(hint)))
    if origin is not None:
        return f"{_type_name(origin)}[{', '.join(map(_type_name, get_args(hint))) or '()'}]"
    if inspect.isclass(hint):
        return qualname(hint)
    return repr(hint).replace("typing.", "")


DI = Dependencies()
