import asyncio
import logging
import os
import signal
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from nuke_di.clients import BackgroundTasks, Shutdown
from nuke_di.core import Dependencies
from nuke_di.errors import InitializeDependencyError, UsageError
from nuke_di.timings import ClientTiming

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_ENV = "SHUTDOWN_GRACE_SECONDS"
DEFAULT_SHUTDOWN_GRACE = 10.0

SIGNALS = (signal.SIGTERM, signal.SIGINT)

Kind = Literal["job", "worker"]


def _shutdown_grace_from_env() -> float:
    return float(os.environ.get(SHUTDOWN_GRACE_ENV, DEFAULT_SHUTDOWN_GRACE))


@dataclass
class RunSettings:
    # How long an entrypoint may keep running after a Shutdown begins
    shutdown_grace: float = field(default_factory=_shutdown_grace_from_env)

    def __post_init__(self) -> None:
        if self.shutdown_grace < 0:
            raise ValueError(f"shutdown_grace must be >= 0, got {self.shutdown_grace}")


@dataclass
class Run:
    """
    One execution of an entrypoint, from resolving its clients to the exit code.
    """

    name: str
    kind: Kind
    started_at: datetime
    finished_at: datetime | None = None
    exit_code: int | None = None
    # The exception that failed the Run
    error: BaseException | None = None
    # The first termination signal received
    signal: int | None = None
    # How every client connected and disconnected; empty when the Run failed before connecting
    clients: list[ClientTiming] = field(default_factory=list)


class RunHook(Protocol):
    async def on_start(self, run: Run) -> None: ...

    async def on_finish(self, run: Run) -> None: ...


async def run_entrypoint(
    func: Callable[..., Coroutine[Any, Any, Any]],
    *,
    kind: Kind,
    name: str,
    hooks: Sequence[RunHook],
    container: Dependencies,
    settings: RunSettings,
    params: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
) -> Run:
    """
    Run `func` with its clients and `params`; an `error` found while parsing the command line fails the Run at once.
    """
    run = Run(name=name, kind=kind, started_at=datetime.now(UTC))
    extra = {"run": name}
    logger.info("Starting %s %s", kind, name, extra=extra)

    for hook in hooks:
        await _call_hook(hook, "on_start", run)

    if error is not None:
        run.error = error
    else:
        await _Runner(run, container, settings).execute(func, params or {})

    run.finished_at = datetime.now(UTC)
    run.exit_code = _exit_code(run)
    if isinstance(run.error, UsageError):
        # argparse has already printed the reason, a traceback of the parser is noise
        logger.error("Run %s failed: %s", name, run.error, extra=extra)
    elif run.error is not None:
        logger.error("Run %s failed", name, exc_info=run.error, extra=extra)
    duration = (run.finished_at - run.started_at).total_seconds()
    logger.info(
        "Run %s finished with exit code %d in %.3fs",
        name,
        run.exit_code,
        duration,
        extra={**extra, "duration": duration},
    )

    for hook in reversed(hooks):
        await _call_hook(hook, "on_finish", run)

    return run


async def _call_hook(hook: RunHook, method: str, run: Run) -> None:
    try:
        await getattr(hook, method)(run)
    except Exception:
        # A hook observes the Run, it never changes its outcome
        logger.exception("Hook %r failed in %s", hook, method, extra={"run": run.name})


def _exit_code(run: Run) -> int:
    if isinstance(run.error, UsageError):
        return 2
    if run.error is not None:
        return 1
    if run.signal is not None:
        return 128 + run.signal
    return 0


async def _guarded(coro: Coroutine[Any, Any, Any]) -> BaseException | None:
    """
    Return the exception of `coro` instead of raising it.

    A `SystemExit` raised inside a task escapes the event loop, and the container's errors are `SystemExit`.
    """
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        return exc
    return None


class _Runner:
    def __init__(self, run: Run, container: Dependencies, settings: RunSettings) -> None:
        self.run = run
        self.container = container
        self.settings = settings
        self._shutdown = Shutdown()
        # The task a signal or a failed background task cancels: connect, then the entrypoint
        self._current: asyncio.Task[BaseException | None] | None = None
        self._woken = asyncio.Event()
        # The structured fields of every log record about this Run
        self._extra = {"run": run.name}

    async def execute(self, func: Callable[..., Coroutine[Any, Any, Any]], params: Mapping[str, Any]) -> None:
        restore = self._install_signal_handlers(asyncio.get_running_loop())
        try:
            await self._execute(func, params)
        finally:
            restore()

    async def _execute(self, func: Callable[..., Coroutine[Any, Any, Any]], params: Mapping[str, Any]) -> None:
        try:
            self._shutdown = self.container.resolve(Shutdown)
            tasks = self.container.resolve(BackgroundTasks)
            injected = self.container.inject(func)
        except (Exception, InitializeDependencyError) as exc:
            self.container.flush()
            self._fail(exc)
            return

        tasks.watch(self._on_background_failure)

        # A connect cancelled before its first step would leave the timings of an earlier connect
        self.container.timings = []
        connect = await self._phase(self.container.connect())
        if self._record(connect):
            entrypoint = await self._phase(injected(**params), stoppable=True)
            self._record(entrypoint)

        # Background tasks use the clients, so they stop first
        await tasks.stop()
        if self.container.connected:
            await self.container.disconnect()
        self.run.clients = self.container.timings

    async def _phase(
        self, coro: Coroutine[Any, Any, Any], stoppable: bool = False
    ) -> asyncio.Task[BaseException | None]:
        task = self._current = asyncio.create_task(_guarded(coro))
        woken = asyncio.create_task(self._woken.wait())
        try:
            await asyncio.wait({task, woken}, return_when=asyncio.FIRST_COMPLETED)
            if stoppable and not task.done() and self.run.error is None:
                await self._grace(task)
            if not task.done():
                task.cancel()
            await asyncio.wait({task})
        finally:
            woken.cancel()
            self._current = None
        return task

    async def _grace(self, task: asyncio.Task[BaseException | None]) -> None:
        grace = self.settings.shutdown_grace
        done, _ = await asyncio.wait({task}, timeout=grace)
        if not done:
            logger.warning(
                "Run %s did not stop within %ss after Shutdown, cancelling it", self.run.name, grace, extra=self._extra
            )

    def _record(self, task: asyncio.Task[BaseException | None]) -> bool:
        """
        Record the outcome of a phase; return whether it completed.
        """
        if task.cancelled():
            return False
        exc = task.result()
        if exc is not None:
            self._fail(exc)
            return False
        return True

    def _fail(self, exc: BaseException) -> None:
        if self.run.error is None:
            self.run.error = exc

    def _on_background_failure(self, exc: BaseException) -> None:
        self._fail(exc)
        self._woken.set()

    def _on_signal(self, signum: int) -> None:
        current = self._current
        if current is None or current.done():
            # Before connect or after the entrypoint ended: disconnect is bounded by its own timeout
            return

        if self.run.signal is None:
            self.run.signal = signum
            logger.info("Shutdown requested by %s", signal.Signals(signum).name, extra=self._extra)
            self._shutdown.set()
            self._woken.set()
        else:
            logger.warning(
                "Second %s, cancelling run %s", signal.Signals(signum).name, self.run.name, extra=self._extra
            )
            current.cancel()

    def _install_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> Callable[[], None]:
        previous = {sig: signal.getsignal(sig) for sig in SIGNALS}

        try:
            for sig in SIGNALS:
                loop.add_signal_handler(sig, self._on_signal, sig)
        except NotImplementedError:
            # Windows: the event loop cannot handle signals, and only SIGINT can be caught
            signal.signal(signal.SIGINT, lambda signum, frame: loop.call_soon_threadsafe(self._on_signal, signum))
            return lambda: _restore(signal.SIGINT, previous[signal.SIGINT])

        def restore() -> None:
            for sig in SIGNALS:
                loop.remove_signal_handler(sig)
                _restore(sig, previous[sig])

        return restore


def _restore(sig: signal.Signals, handler: Any) -> None:
    # `getsignal` returns None for a handler that was not installed from Python
    signal.signal(sig, handler or signal.SIG_DFL)
