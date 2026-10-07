from collections.abc import AsyncGenerator
from unittest.mock import call

import pytest

from nuke_di import DI, Client, Dependencies


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

    assert mocked_client.add.await_args_list == [call(1, 2)]  # type: ignore


@pytest.fixture()
async def app_like_fixture() -> AsyncGenerator[Dependencies, None]:
    DI.resolve(PublicClient)
    async with DI:
        yield DI


@pytest.fixture
async def mock_client() -> PublicClient:
    """
    Example of a fixture that mocks a dependency.

    This fixture must come first in the test arguments or via @pytest.mark.usefixtures,
    so that the mock is registered before the dependency tree is resolved.
    """
    return DI.mock(PublicClient)


async def test_with_fixture(mock_client: PublicClient, app_like_fixture: Dependencies) -> None:
    cli: PublicClient = app_like_fixture.clients[PublicClient]  # type: ignore

    await cli.add(1, 2)

    assert mock_client.add.await_args_list == [call(1, 2)]  # type: ignore
