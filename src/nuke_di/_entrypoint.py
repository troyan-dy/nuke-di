import asyncio
import inspect
import sys
from collections.abc import Callable, Coroutine
from typing import Any, TypeAlias, TypeVar

from nuke_di.core import DI

_AsyncFunction: TypeAlias = Callable[..., Coroutine[Any, Any, Any]]
FuncT = TypeVar("FuncT", bound=_AsyncFunction)


def entrypoint(func: FuncT, /) -> FuncT:
    """Run an async function with its clients when its module is executed directly."""
    if not inspect.iscoroutinefunction(func):
        raise TypeError(f'Entrypoint "{func.__qualname__}" must be declared with "async def"')
    if func.__module__ == "__main__":
        if len(sys.argv) > 1:
            sys.exit("This entrypoint does not accept command-line arguments")
        asyncio.run(_run(func))
        sys.exit()
    return func


async def _run(func: _AsyncFunction) -> None:
    injected = DI.inject(func)
    inspect.signature(injected).bind()
    async with DI:
        await injected()
