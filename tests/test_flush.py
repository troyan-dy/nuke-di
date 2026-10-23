import pytest

from nuke_di import Client, Dependencies, NotSingletonClient
from nuke_di.errors import ConnectError


class InnerDeps(NotSingletonClient):
    """A dependency every consumer gets its own instance of."""


class PublicClient(Client):
    def __init__(self, inner: InnerDeps):
        self.inner = inner

    async def add(self, left: int, right: int) -> int:
        return left + right


class NewPublicClient(PublicClient):
    """A second client class with the same dependency, for an instance of InnerDeps of its own."""


async def test_cached_client() -> None:
    dep = Dependencies()

    first_client = dep.resolve(PublicClient)
    second_client = dep.resolve(PublicClient)

    assert first_client is second_client


async def test_not_singleton_client() -> None:
    dep = Dependencies()

    first_client = dep.resolve(PublicClient)
    second_client = dep.resolve(NewPublicClient)

    assert first_client.inner is not second_client.inner


async def test_flush() -> None:
    dep = Dependencies()

    first_client = dep.resolve(PublicClient)
    dep.flush()
    second_client = dep.resolve(PublicClient)

    assert first_client is not second_client


async def test_flush_on_connected() -> None:
    dep = Dependencies()
    dep.resolve(PublicClient)

    async with dep:
        with pytest.raises(ConnectError):
            dep.flush()
