import asyncio
import inspect
import sys
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any, TypeVar, overload

from nuke_di.core import DI
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
    settings = RunSettings()
    run = asyncio.run(
        run_entrypoint(func, kind=kind, name=entrypoint_name(func), hooks=hooks, container=DI, settings=settings)
    )
    return run.exit_code or 0


def entrypoint_name(func: Callable[..., Any]) -> str:
    module = func.__module__
    if module == "__main__":
        main = sys.modules["__main__"]
        # `python -m app.jobs.sync` keeps the importable name in the spec, `python app/jobs/sync.py` has none
        module = main.__spec__.name if main.__spec__ is not None else Path(main.__file__ or "").stem
    return f"{module}.{func.__qualname__}"
