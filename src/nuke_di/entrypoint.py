import asyncio
import inspect
import sys
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any, TypeVar, overload

from nuke_di.core import DI
from nuke_di.errors import UsageError
from nuke_di.run import Kind, RunHook, RunSettings, run_entrypoint

F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])


@overload
def job(func: F, /) -> F: ...


@overload
def job(*, hooks: Sequence[RunHook] = ()) -> Callable[[F], F]: ...


def job(func: F | None = None, /, *, hooks: Sequence[RunHook] = ()) -> F | Callable[[F], F]:
    """
    Declare a job: an entrypoint that runs once.

    When the module is run as `__main__`, the job runs right here and the process exits;
    code below the decorated function never runs. On a normal import `func` is returned unchanged.
    """
    return _entrypoint("job", func, hooks)


@overload
def worker(func: F, /) -> F: ...


@overload
def worker(*, hooks: Sequence[RunHook] = ()) -> Callable[[F], F]: ...


def worker(func: F | None = None, /, *, hooks: Sequence[RunHook] = ()) -> F | Callable[[F], F]:
    """
    Declare a worker: an entrypoint that runs until the process is asked to stop.

    When the module is run as `__main__`, the worker runs right here and the process exits;
    code below the decorated function never runs. On a normal import `func` is returned unchanged.
    """
    return _entrypoint("worker", func, hooks)


def _entrypoint(kind: Kind, func: F | None, hooks: Sequence[RunHook]) -> F | Callable[[F], F]:
    def decorate(f: F) -> F:
        if not inspect.iscoroutinefunction(f):
            raise TypeError(f'{kind} "{f.__qualname__}" must be declared with "async def"')
        if f.__module__ == "__main__":
            sys.exit(execute(f, kind=kind, hooks=hooks))
        return f

    return decorate if func is None else decorate(func)


def execute(func: Callable[..., Coroutine[Any, Any, Any]], *, kind: Kind, hooks: Sequence[RunHook]) -> int:
    # argparse costs every process a few milliseconds at import, and only a process that runs an entrypoint parses
    from nuke_di.cli import HelpRequested, build_parser, parse_parameters

    settings = RunSettings()
    params: dict[str, Any] = {}
    error: BaseException | None = None

    try:
        parser = build_parser(func, prog=entrypoint_prog())
        try:
            params = parse_parameters(parser, sys.argv[1:])
        except UsageError as exc:
            # Only parsing raises it, so the parser exists here; the Run still fails below
            sys.stderr.write(f"{exc.usage}{parser.prog}: error: {exc}\n")
            raise
    except HelpRequested:
        # Printing the help is not a Run: no hook sees it
        return 0
    except Exception as exc:
        # A broken signature or command line fails the Run, so that hooks see it
        error = exc

    run = asyncio.run(
        run_entrypoint(
            func,
            kind=kind,
            name=entrypoint_name(func),
            hooks=hooks,
            container=DI,
            settings=settings,
            params=params,
            error=error,
        )
    )
    return run.exit_code or 0


def entrypoint_name(func: Callable[..., Any]) -> str:
    module = func.__module__
    if module == "__main__":
        main = sys.modules["__main__"]
        # `python -m app.jobs.sync` keeps the importable name in the spec, `python app/jobs/sync.py` has none
        module = main.__spec__.name if main.__spec__ is not None else Path(main.__file__ or "").stem
    return f"{module}.{func.__qualname__}"


def entrypoint_prog() -> str | None:
    """
    The command shown in `--help`: `python -m <module>`, or the argparse default for a file run by path.
    """
    spec = sys.modules["__main__"].__spec__
    if spec is None:
        return None
    return f"python -m {spec.name.removesuffix('.__main__')}"
