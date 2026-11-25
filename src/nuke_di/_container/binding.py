"""Bind a callable to the lifetime of the clients captured by its partial."""

import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from contextlib import aclosing
from functools import partial, update_wrapper
from typing import Any, TypeVar, cast

from nuke_di._container.registry import BindingLifetime
from nuke_di.naming import sname

ReturnT = TypeVar("ReturnT")


def guarded_binding(
    bound: partial[ReturnT],
    lifetime: BindingLifetime,
) -> Callable[..., ReturnT]:
    func = bound.func
    invoke: partial[Any]
    if inspect.iscoroutinefunction(func):
        asynchronous = cast(partial[Awaitable[Any]], bound)
        invoke = partial(_call_async, asynchronous, lifetime)
    elif inspect.isgeneratorfunction(func):
        generator = cast(partial[Generator[Any, Any, Any]], bound)
        invoke = partial(_call_generator, generator, lifetime)
    elif inspect.isasyncgenfunction(func):
        async_generator = cast(partial[AsyncGenerator[Any, Any]], bound)
        invoke = partial(_call_async_generator, async_generator, lifetime)
    else:
        invoke = partial(_call, bound, lifetime)
    return cast(Callable[..., ReturnT], update_wrapper(invoke, bound))


def _call(
    bound: partial[ReturnT],
    lifetime: BindingLifetime,
    /,
    *args: Any,
    **kwargs: Any,
) -> ReturnT:
    if not lifetime.valid:
        raise _stale_binding(bound)
    return bound(*args, **kwargs)


async def _call_async(
    bound: partial[Awaitable[ReturnT]],
    lifetime: BindingLifetime,
    /,
    *args: Any,
    **kwargs: Any,
) -> ReturnT:
    if not lifetime.valid:
        raise _stale_binding(bound)
    return await bound(*args, **kwargs)


def _call_generator(
    bound: partial[Generator[Any, Any, ReturnT]],
    lifetime: BindingLifetime,
    /,
    *args: Any,
    **kwargs: Any,
) -> Generator[Any, Any, ReturnT]:
    if not lifetime.valid:
        raise _stale_binding(bound)
    return (yield from bound(*args, **kwargs))


async def _call_async_generator(
    bound: partial[AsyncGenerator[Any, Any]],
    lifetime: BindingLifetime,
    /,
    *args: Any,
    **kwargs: Any,
) -> AsyncGenerator[Any, Any]:
    async with aclosing(_call(bound, lifetime, *args, **kwargs)) as generator:
        next_value: Awaitable[Any] = anext(generator)
        while True:
            try:
                yielded = await next_value
            except StopAsyncIteration:
                return
            # Async generators have no yield from. Forward the generator protocol.
            try:
                sent = yield yielded
            except GeneratorExit:
                raise
            except BaseException as exc:
                next_value = generator.athrow(exc)
            else:
                next_value = generator.asend(sent)


def _stale_binding(bound: partial[Any]) -> RuntimeError:
    name = sname(bound.func)
    return RuntimeError(f'Injected function "{name}" belongs to a flushed container; inject again before calling it')
