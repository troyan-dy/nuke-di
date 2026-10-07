import pytest

from nuke_di import Client, Dependencies, NotSingletonClient
from nuke_di.errors import ConnectError


class InnerDeps(NotSingletonClient):
    pass


class PublicClient(Client):
    def __init__(self, inner: InnerDeps):
        self.inner = inner

    async def add(self, a: int, b: int) -> int:
        return a + b


class NewPublicClient(PublicClient):
    pass


async def test_cached_client() -> None:
    dep = Dependencies()

    client_1 = dep.resolve(PublicClient)
    client_2 = dep.resolve(PublicClient)

    assert client_1 is client_2


async def test_not_singleton_client() -> None:
    dep = Dependencies()

    client_1 = dep.resolve(PublicClient)
    client_2 = dep.resolve(NewPublicClient)

    assert client_1.inner is not client_2.inner


async def test_flush() -> None:
    dep = Dependencies()

    client_1 = dep.resolve(PublicClient)
    dep.flush()
    client_2 = dep.resolve(PublicClient)

    assert client_1 is not client_2


async def test_flush_on_connected() -> None:
    dep = Dependencies()
    dep.resolve(PublicClient)

    async with dep:
        with pytest.raises(ConnectError):
            dep.flush()
