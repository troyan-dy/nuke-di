import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from nuke_di._runner import _AsyncFunction, _Runner, exit_code
from nuke_di.core import Dependencies
from nuke_di.errors import UsageError
from nuke_di.logs import current_run, fields
from nuke_di.timings import ClientTiming

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_ENV = "SHUTDOWN_GRACE_SECONDS"
DEFAULT_SHUTDOWN_GRACE = 10.0

Kind = Literal["job", "worker"]


@dataclass
class RunSettings:
    # How long an entrypoint may keep running after a Shutdown begins
    shutdown_grace: float = field(
        default_factory=lambda: float(os.environ.get(SHUTDOWN_GRACE_ENV, DEFAULT_SHUTDOWN_GRACE)),
    )

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


@dataclass(frozen=True)
class RunRequest:
    """
    What to run: an entrypoint, with the container of its clients, and the hooks that see the Run.
    """

    func: _AsyncFunction
    kind: Kind
    name: str
    hooks: Sequence[RunHook]
    container: Dependencies
    settings: RunSettings


async def run_entrypoint(
    request: RunRequest,
    *,
    parameter_values: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
) -> Run:
    """
    Run the entrypoint of `request` with its clients and `parameter_values`; an `error` found while parsing the
    command line fails the Run at once.
    """
    started_at = datetime.now(UTC)
    run = Run(name=request.name, kind=request.kind, started_at=started_at)
    # Every log record made inside the Run carries its name, the container's included
    token = current_run.set(request.name)
    try:
        await _run(run, request, parameter_values, error)
    except BaseException:
        current_run.reset(token)
        raise
    current_run.reset(token)
    return run


async def _run(
    run: Run,
    request: RunRequest,
    parameter_values: Mapping[str, Any] | None,
    error: BaseException | None,
) -> None:
    name, kind = run.name, run.kind
    logger.info("Starting %s %s", kind, name, extra=fields())
    await _call_hooks(request.hooks, "on_start", run)

    if error is None:
        runner = _Runner(run, request.container, request.settings)
        await runner.execute(request.func, parameter_values or {})
    else:
        run.error = error

    run.finished_at = datetime.now(UTC)
    run.exit_code = exit_code(run)
    if isinstance(run.error, UsageError):
        # argparse has already printed the reason, a traceback of the parser is noise
        logger.error("Run %s failed: %s", name, run.error, extra=fields())
    elif run.error is not None:
        logger.error("Run %s failed", name, exc_info=run.error, extra=fields())
    duration = (run.finished_at - run.started_at).total_seconds()
    logger.info(
        "Run %s finished with exit code %d in %.3fs",
        name,
        run.exit_code,
        duration,
        extra=fields(duration=duration),
    )

    await _call_hooks(reversed(request.hooks), "on_finish", run)


async def _call_hooks(hooks: Iterable[RunHook], method: str, run: Run) -> None:
    for hook in hooks:
        try:
            # One at a time: on_start runs in the order of the hooks and on_finish in reverse, by contract
            await getattr(hook, method)(run)  # noqa: WPS476
        except Exception:
            # A hook observes the Run, it never changes its outcome
            logger.exception("Hook %r failed in %s", hook, method, extra=fields())
