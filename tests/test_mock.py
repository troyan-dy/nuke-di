from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import NonCallableMock, call

import pytest

from nuke_di import DI, Client, ConnectError, Dependencies, NotSingletonClient


class InnerDeps(Client):
    pass


class PublicClient(Client):
    def __init__(self, inner: InnerDeps):
        self.inner = inner

    async def add(self, a: int, b: int) -> int:
        return a + b


async def test_cached_client() -> None:
    dep = Dependencies()

    mocked_client = dep.mock(PublicClient)

    client_1 = dep.resolve(PublicClient)

    async with dep:
        await client_1.add(1, 2)

    assert mocked_client.add.await_args_list == [call(1, 2)]


@pytest.fixture()
async def app_like_fixture() -> AsyncGenerator[Dependencies, None]:
    DI.resolve(PublicClient)
    async with DI:
        yield DI


@pytest.fixture
async def mock_client() -> Any:
    """
    Example of a fixture that mocks a dependency.

    This fixture must come first in the test arguments or via @pytest.mark.usefixtures,
    so that the mock is registered before the dependency tree is resolved.
    """
    return DI.mock(PublicClient)


async def test_with_fixture(mock_client: Any, app_like_fixture: Dependencies) -> None:
    cli = app_like_fixture.clients[PublicClient]
    assert isinstance(cli, PublicClient)

    await cli.add(1, 2)

    assert mock_client.add.await_args_list == [call(1, 2)]


def test_mock_is_an_instance_of_the_class() -> None:
    dep = Dependencies()

    mocked = dep.mock(PublicClient)

    # A Replacement stands in for an instance, so a call is a mistake rather than another mock
    with pytest.raises(TypeError, match="'NonCallableMagicMock' object is not callable"):
        mocked()
    assert isinstance(mocked, NonCallableMock)
    assert isinstance(mocked, PublicClient)


async def test_mock_keeps_async_methods_awaitable() -> None:
    dep = Dependencies()

    mocked = dep.mock(PublicClient)
    mocked.add.return_value = 3

    assert await mocked.add(1, 2) == 3
    mocked.add.assert_awaited_once_with(1, 2)


class Session(NotSingletonClient):
    pass


class UsesSession(Client):
    def __init__(self, session: Session):
        self.session = session


def test_mock_after_resolve_raises() -> None:
    dep = Dependencies()
    dep.resolve(PublicClient)

    with pytest.raises(ConnectError, match=r"InnerDeps is already resolved, call mock\(\) before resolve\(\)"):
        dep.mock(InnerDeps)


def test_mock_not_singleton_after_resolve_raises() -> None:
    dep = Dependencies()
    dep.resolve(UsesSession)

    with pytest.raises(ConnectError, match="Session is already resolved"):
        dep.mock(Session)


def test_mock_twice_returns_the_same_mock() -> None:
    dep = Dependencies()

    assert dep.mock(InnerDeps) is dep.mock(InnerDeps)


def test_mock_with_another_replacement_raises() -> None:
    dep = Dependencies()
    dep.mock(InnerDeps)

    with pytest.raises(ConnectError, match="InnerDeps already has a replacement"):
        dep.mock(InnerDeps, InnerDeps())


def test_mock_after_flush() -> None:
    dep = Dependencies()
    dep.resolve(PublicClient)
    dep.flush()

    inner = dep.mock(InnerDeps)

    assert dep.resolve(PublicClient).inner is inner
