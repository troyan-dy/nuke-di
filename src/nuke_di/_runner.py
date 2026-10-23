"""
The execution of one Run: the clients connected, the entrypoint run, both stopped by a signal or a failed
background task, and the signal handlers installed for the time it runs.
"""

import asyncio
import logging
import signal
from collections.abc import Callable, Coroutine, Mapping
from functools import partial
from typing import TYPE_CHECKING, Any, Final, TypeAlias

from nuke_di.clients import BackgroundTasks, Shutdown
from nuke_di.core import Dependencies
from nuke_di.errors import InitializeDependencyError, UsageError
from nuke_di.logs import fields

if TYPE_CHECKING:
    from nuke_di.run import Run, RunSettings

# The records of a Run keep the logger of nuke_di.run, where the Runner was declared
logger = logging.getLogger("nuke_di.run")

SIGNALS = (signal.SIGTERM, signal.SIGINT)

# A process killed by a signal exits with 128 + the number of the signal, as a shell reports it
_SIGNAL_EXIT_BASE: Final = 128

# An entrypoint: an `async def`
_AsyncFunction: TypeAlias = Callable[..., Coroutine[Any, Any, Any]]


class _Runner:
    def __init__(self, run: "Run", container: Dependencies, settings: "RunSettings") -> None:
        self.run = run
        self.container = container
        self.settings = settings
        self._shutdown = Shutdown()
        # The task a signal or a failed background task cancels: connect, then the entrypoint
        self._current: asyncio.Task[BaseException | None] | None = None
        self._woken = asyncio.Event()

    async def execute(self, func: _AsyncFunction, parameter_values: Mapping[str, Any]) -> None:
        restore = install_signal_handlers(asyncio.get_running_loop(), self._on_signal)
        try:
            await self._execute(func, parameter_values)
        except BaseException:
            restore()
            raise
        restore()

    async def _execute(self, func: _AsyncFunction, parameter_values: Mapping[str, Any]) -> None:
        try:
            shutdown, tasks, injected = _resolve_entrypoint(self.container, func)
        except (Exception, InitializeDependencyError) as exc:
            self.container.flush()
            self._fail(exc)
            return
        self._shutdown = shutdown
        tasks.watch(self._on_background_failure)

        # A connect cancelled before its first step would leave the timings of an earlier connect
        self.container.timings = []
        if await self._phase(self.container.connect()):
            await self._phase(injected(**parameter_values), stoppable=True)

        # Background tasks use the clients, so they stop first
        await tasks.stop()
        if self.container.connected:
            await self.container.disconnect()
        self.run.clients = self.container.timings

    async def _phase(self, coro: Coroutine[Any, Any, Any], stoppable: bool = False) -> bool:
        """
        Run one phase as the current task and record its outcome; return whether it completed.
        """
        task = asyncio.create_task(_guarded(coro))
        self._current = task
        try:
            await _settle(self, task, stoppable)
        except BaseException:
            self._current = None
            raise
        self._current = None
        if task.cancelled():
            return False
        exc = task.result()
        if exc is not None:
            self._fail(exc)
        return exc is None

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

        name = signal.Signals(signum).name
        if self.run.signal is None:
            self.run.signal = signum
            logger.info("Shutdown requested by %s", name, extra=fields())
            self._shutdown.set()
            self._woken.set()
        else:
            logger.warning("Second %s, cancelling run %s", name, self.run.name, extra=fields())
            current.cancel()


def exit_code(run: "Run") -> int:
    if isinstance(run.error, UsageError):
        return 2
    if run.error is not None:
        return 1
    if run.signal is not None:
        return _SIGNAL_EXIT_BASE + run.signal
    return 0


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    on_signal: Callable[[int], None],
) -> Callable[[], None]:
    """
    Call `on_signal(signum)` on SIGTERM and SIGINT; return what restores the handlers there were before.
    """
    previous = {sig: signal.getsignal(sig) for sig in SIGNALS}

    try:
        for sig in SIGNALS:
            loop.add_signal_handler(sig, on_signal, sig)
    except NotImplementedError:
        # Windows: the event loop cannot handle signals, and only SIGINT can be caught
        signal.signal(signal.SIGINT, lambda signum, frame: loop.call_soon_threadsafe(on_signal, signum))
        return partial(_restore, {signal.SIGINT: previous[signal.SIGINT]}, None)
    return partial(_restore, previous, loop)


def _restore(previous: Mapping[signal.Signals, Any], loop: asyncio.AbstractEventLoop | None) -> None:
    """
    Put back the `previous` handlers of the signals, removing those of `loop` first.
    """
    for sig, handler in previous.items():
        if loop is not None:
            loop.remove_signal_handler(sig)
        # `getsignal` returns None for a handler that was not installed from Python
        signal.signal(sig, handler or signal.SIG_DFL)


def _resolve_entrypoint(container: Dependencies, func: _AsyncFunction) -> tuple[Shutdown, BackgroundTasks, Any]:
    """
    The clients a Run itself uses, and `func` with its clients.
    """
    return container.resolve(Shutdown), container.resolve(BackgroundTasks), container.inject(func)


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


async def _settle(runner: _Runner, task: asyncio.Task[BaseException | None], stoppable: bool) -> None:
    """
    Wait for `task` to end, or for a signal or a failed background task to wake the runner first: then give a
    stoppable task the grace of a Shutdown, and cancel it.
    """
    woken = asyncio.create_task(runner._woken.wait())
    try:
        await asyncio.wait({task, woken}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        woken.cancel()
        raise
    woken.cancel()
    if stoppable and not task.done() and runner.run.error is None:
        grace = runner.settings.shutdown_grace
        done, _ = await asyncio.wait({task}, timeout=grace)
        if not done:
            logger.warning(
                "Run %s did not stop within %ss after Shutdown, cancelling it", runner.run.name, grace, extra=fields()
            )
    if not task.done():
        task.cancel()
    await asyncio.wait({task})
