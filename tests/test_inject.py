import inspect
from collections.abc import AsyncGenerator, Generator

import pytest

from nuke_di import Client, Dependencies, InvalidSignatureError


class InnerDeps(Client):
    def number(self) -> int:
        return 1


class PublicClient(Client):
    def __init__(self, inner: InnerDeps):
        self.inner = inner

    async def add(self, left: int, right: int) -> int:
        return left + right


async def any_func(number: int, adder: PublicClient, inner: InnerDeps) -> int:
    assert adder.inner is inner

    return await adder.add(number, inner.number())


async def test_inject() -> None:
    dep = Dependencies()
    new_func = dep.inject(any_func)

    async with dep:
        assert (await new_func(1)) == 2


def test_inject_rejects_argument_without_type_hint() -> None:
    def func(number, inner: InnerDeps) -> None:  # type: ignore[no-untyped-def]
        ...

    with pytest.raises(InvalidSignatureError, match='"number"'):
        Dependencies().inject(func)  # type: ignore[nuke-di]


def test_inject_allows_untyped_var_arguments() -> None:
    def func(*args, inner: InnerDeps, **kwargs) -> InnerDeps:  # type: ignore[no-untyped-def]
        return inner

    assert Dependencies().inject(func)().__class__ is InnerDeps


class Request:
    """An argument supplied by the application."""

    def __init__(self, number: int) -> None:
        self.number = number


class OptionalClient(Client):
    def __init__(self, inner: InnerDeps | None = None) -> None:
        self.inner = inner


def optional_client(inner: InnerDeps | None = None) -> None: ...


def positional_client(inner: InnerDeps, /) -> None: ...


def test_optional_client_is_invalid_even_with_a_default() -> None:
    dep = Dependencies()
    with pytest.raises(InvalidSignatureError, match="a client cannot be optional"):
        dep.resolve(OptionalClient)  # type: ignore[nuke-di]
    with pytest.raises(InvalidSignatureError, match="a client cannot be optional"):
        dep.inject(optional_client)  # type: ignore[nuke-di]
    assert not dep.clients


def test_positional_only_client_fails_at_binding() -> None:
    dep = Dependencies()
    with pytest.raises(InvalidSignatureError, match="is positional-only, a client is passed by keyword"):
        dep.inject(positional_client)  # type: ignore[nuke-di]
    assert not dep.clients


def test_plain_arguments_are_still_supplied_by_the_caller() -> None:
    def handler(request: Request, inner: InnerDeps) -> int:
        return request.number + inner.number()

    dep = Dependencies()
    assert dep.inject(handler)(Request(4)) == 5


def test_injected_function_is_stale_after_flush() -> None:
    seen: list[InnerDeps] = []

    def handler(inner: InnerDeps) -> InnerDeps:
        seen.append(inner)
        return inner

    dep = Dependencies()
    bound = dep.inject(handler)
    first = bound()
    dep.flush()

    with pytest.raises(RuntimeError, match=r"handler.*inject again"):
        bound()
    assert seen == [first]
    assert dep.inject(handler)() is not first


async def test_injected_coroutine_checks_its_binding_when_awaited() -> None:
    seen: list[InnerDeps] = []

    async def handler(inner: InnerDeps, label: str = "default") -> str:
        seen.append(inner)
        return label

    dep = Dependencies()
    bound = dep.inject(handler)
    assert inspect.iscoroutinefunction(bound)
    assert inspect.signature(bound).bind()
    pending = bound()

    async with dep:
        assert await bound(label="ready") == "ready"

    dep.resolve(InnerDeps)
    async with dep:
        with pytest.raises(RuntimeError, match=r"handler.*inject again"):
            await pending
        with pytest.raises(RuntimeError, match=r"handler.*inject again"):
            await bound()

    assert len(seen) == 1
    assert await dep.inject(handler)() == "default"


def test_function_without_injected_clients_is_still_callable_after_flush() -> None:
    def handler(request: Request) -> int:
        return request.number

    dep = Dependencies()
    bound = dep.inject(handler)
    dep.flush()
    assert bound(Request(7)) == 7


def stream(inner: InnerDeps) -> Generator[int, None, None]:
    yield inner.number()


async def async_stream(inner: InnerDeps) -> AsyncGenerator[int, int | None]:
    try:
        sent = yield inner.number()
    except LookupError:
        yield -1
    else:
        yield sent or 2


def test_injected_generator_checks_binding_before_first_iteration() -> None:
    dep = Dependencies()
    injected = dep.inject(stream)
    assert inspect.isgeneratorfunction(injected)
    assert list(injected()) == [1]
    pending = injected()
    dep.flush()
    with pytest.raises(RuntimeError, match=r"stream.*inject again"):
        next(pending)


async def test_injected_async_generator_checks_binding_before_first_iteration() -> None:
    dep = Dependencies()
    injected = dep.inject(async_stream)
    assert inspect.isasyncgenfunction(injected)
    pending = injected()
    dep.flush()
    with pytest.raises(RuntimeError, match=r"async_stream.*inject again"):
        await anext(pending)


async def test_injected_async_generator_forwards_iteration_and_throw() -> None:
    dep = Dependencies()
    injected = dep.inject(async_stream)
    assert [number async for number in injected()] == [1, 2]

    generator = injected()
    assert await anext(generator) == 1
    assert await generator.athrow(LookupError()) == -1
    await generator.aclose()


async def test_injected_async_generator_forwards_sent_values() -> None:
    generator = Dependencies().inject(async_stream)()
    assert await anext(generator) == 1
    assert await generator.asend(7) == 7
    await generator.aclose()


def test_inject_validates_clients_declared_in_a_class_constructor() -> None:
    class NeedsClient:
        def __init__(self, inner: InnerDeps) -> None:
            self.inner = inner

    dep = Dependencies()
    assert dep.inject(NeedsClient)().inner is dep.resolve(InnerDeps)
    with pytest.raises(InvalidSignatureError, match="a client cannot be optional"):
        dep.inject(OptionalClient)  # type: ignore[nuke-di]
