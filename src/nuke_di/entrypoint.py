import asyncio
import inspect
import sys
from collections.abc import Sequence
from typing import Any, Protocol, TypeVar, cast, overload

from nuke_di._program import MAIN, entrypoint_name, entrypoint_prog
from nuke_di._runner import _AsyncFunction
from nuke_di.core import DI
from nuke_di.errors import UsageError
from nuke_di.run import Kind, RunHook, RunRequest, RunSettings, run_entrypoint

FuncT = TypeVar("FuncT", bound=_AsyncFunction)


class EntrypointDecorator(Protocol):
    """
    What `job(hooks=...)` and `worker(hooks=...)` return: a decorator that keeps the type of the function.

    A class, not a `Callable[[FuncT], FuncT]`: applying it is a call of `__call__`, which the mypy plugin of nuke_di
    checks.
    """

    def __call__(self, func: FuncT, /) -> FuncT: ...


@overload
def job(func: FuncT, /) -> FuncT: ...


@overload
def job(*, hooks: Sequence[RunHook] = ()) -> EntrypointDecorator: ...


def job(func: FuncT | None = None, /, *, hooks: Sequence[RunHook] = ()) -> FuncT | EntrypointDecorator:
    """
    Declare a job: an entrypoint that runs once.

    When the module is run as `__main__`, the job runs right here and the process exits;
    code below the decorated function never runs. On a normal import `func` is returned unchanged.
    """
    return _entrypoint("job", func, hooks)


@overload
def worker(func: FuncT, /) -> FuncT: ...


@overload
def worker(*, hooks: Sequence[RunHook] = ()) -> EntrypointDecorator: ...


def worker(func: FuncT | None = None, /, *, hooks: Sequence[RunHook] = ()) -> FuncT | EntrypointDecorator:
    """
    Declare a worker: an entrypoint that runs until the process is asked to stop.

    When the module is run as `__main__`, the worker runs right here and the process exits;
    code below the decorated function never runs. On a normal import `func` is returned unchanged.
    """
    return _entrypoint("worker", func, hooks)


def _entrypoint(kind: Kind, func: FuncT | None, hooks: Sequence[RunHook]) -> FuncT | EntrypointDecorator:
    def decorator(entrypoint: FuncT) -> FuncT:
        if not inspect.iscoroutinefunction(entrypoint):
            raise TypeError(f'{kind} "{entrypoint.__qualname__}" must be declared with "async def"')
        if entrypoint.__module__ == MAIN:
            sys.exit(execute(entrypoint, kind=kind, hooks=hooks))
        return entrypoint

    # `decorator` takes the FuncT of this call; returned without a function, it takes the FuncT of the call it is
    # applied in
    return cast(EntrypointDecorator, decorator) if func is None else decorator(func)


def execute(func: _AsyncFunction, *, kind: Kind, hooks: Sequence[RunHook]) -> int:
    # argparse costs every process a few milliseconds at import, and only a process that runs an entrypoint parses
    from nuke_di.cli import HelpRequested

    settings = RunSettings()
    parameter_values: dict[str, Any] = {}
    error: BaseException | None = None

    try:
        parameter_values = _parse_command_line(func, sys.argv[1:])
    except HelpRequested:
        # Printing the help is not a Run: no hook sees it
        return 0
    except Exception as exc:
        # A broken signature or command line fails the Run, so that hooks see it
        error = exc

    request = RunRequest(
        func=func,
        kind=kind,
        name=entrypoint_name(func),
        hooks=hooks,
        container=DI,
        settings=settings,
    )
    run = run_entrypoint(request, parameter_values=parameter_values, error=error)
    return asyncio.run(run).exit_code or 0


def _parse_command_line(func: _AsyncFunction, argv: list[str]) -> dict[str, Any]:
    """
    The values of the Parameters of `func` in `argv`; a usage error is printed the way argparse prints it, and raised.
    """
    from nuke_di.cli import build_parser, parse_parameters

    parser = build_parser(func, prog=entrypoint_prog())
    try:
        return parse_parameters(parser, argv)
    except UsageError as exc:
        # Only parsing raises it, so the parser exists here; the Run still fails
        sys.stderr.write(f"{exc.usage}{parser.prog}: error: {exc}\n")
        raise
