import asyncio
import inspect
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar, overload

from nuke_di._runner import _AsyncFunction
from nuke_di.core import DI
from nuke_di.errors import UsageError
from nuke_di.run import Kind, RunHook, RunRequest, RunSettings, run_entrypoint

FuncT = TypeVar("FuncT", bound=_AsyncFunction)

# The name of the module Python runs as the program
_MAIN = "__main__"


class EntrypointDecorator:
    """
    What `job(hooks=...)` and `worker(hooks=...)` return: a decorator that keeps the type of the function.

    A class, not a `Callable[[FuncT], FuncT]`: applying it is a call of `__call__`, which the mypy plugin of nuke_di
    checks.
    """

    def __init__(self, kind: Kind, hooks: Sequence[RunHook]) -> None:
        self.kind: Kind = kind
        self.hooks = hooks

    def __call__(self, func: FuncT, /) -> FuncT:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(f'{self.kind} "{func.__qualname__}" must be declared with "async def"')
        if func.__module__ == _MAIN:
            sys.exit(execute(func, kind=self.kind, hooks=self.hooks))
        return func


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
    decorator = EntrypointDecorator("job", hooks)
    return decorator if func is None else decorator(func)


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
    decorator = EntrypointDecorator("worker", hooks)
    return decorator if func is None else decorator(func)


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


def entrypoint_name(func: Callable[..., Any]) -> str:
    module = func.__module__
    if module == _MAIN:
        main = sys.modules[_MAIN]
        # `python -m app.jobs.sync` keeps the importable name in the spec, `python app/jobs/sync.py` has none
        spec = main.__spec__
        if spec is None:
            module = Path(main.__file__ or "").stem
        else:
            module = spec.name
    return f"{module}.{func.__qualname__}"


def entrypoint_prog() -> str | None:
    """
    The command shown in `--help`: `python -m <module>`, or the argparse default for a file run by path.
    """
    spec = sys.modules[_MAIN].__spec__
    if spec is None:
        return None
    module = spec.name.removesuffix(".__main__")
    return f"python -m {module}"
