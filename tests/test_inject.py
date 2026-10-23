import pytest

from nuke_di import Client, Dependencies, InvalidSignatureError, NotSingletonClient


class InnerDeps(NotSingletonClient):
    def number(self) -> int:
        return 1


class PublicClient(Client):
    def __init__(self, inner: InnerDeps):
        self.inner = inner

    async def add(self, left: int, right: int) -> int:
        return left + right


async def any_func(number: int, adder: PublicClient, inner: InnerDeps) -> int:
    assert adder.inner is not inner

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
