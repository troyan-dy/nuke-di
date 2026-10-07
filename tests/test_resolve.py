from unittest.mock import AsyncMock, call

from nuke_di import Client, Dependencies, NotSingletonClient


class InnerDeps(NotSingletonClient):
    connect = AsyncMock()
    disconnect = AsyncMock()


class PublicClient(Client):
    connect = AsyncMock()
    disconnect = AsyncMock()

    def __init__(self, inner: InnerDeps):
        self.inner = inner


class NewPublicClient(PublicClient):
    connect = AsyncMock()
    disconnect = AsyncMock()


async def test_connecting() -> None:
    dep = Dependencies()
    cli = dep.resolve(PublicClient)
    new_cli = dep.resolve(NewPublicClient)

    assert isinstance(cli, PublicClient)
    assert isinstance(new_cli, NewPublicClient)
    # resolving, not connecting
    assert cli.connect.await_args_list == []
    assert cli.disconnect.await_args_list == []

    assert new_cli.connect.await_args_list == []
    assert new_cli.disconnect.await_args_list == []

    assert cli.inner.connect.await_args_list == []
    assert cli.inner.disconnect.await_args_list == []

    async with dep:
        # connecting
        assert cli.connect.await_args_list == [call()]
        assert cli.disconnect.await_args_list == []

        assert new_cli.connect.await_args_list == [call()]
        assert new_cli.disconnect.await_args_list == []

        assert cli.inner.connect.await_args_list == [call(), call()]
        assert cli.inner.disconnect.await_args_list == []

        assert cli.inner is not new_cli.inner

    # disconnecting
    assert cli.connect.await_args_list == [call()]
    assert cli.disconnect.await_args_list == [call()]

    assert new_cli.connect.await_args_list == [call()]
    assert new_cli.disconnect.await_args_list == [call()]

    assert cli.inner.connect.await_args_list == [call(), call()]
    assert cli.inner.disconnect.await_args_list == [call(), call()]


class TypedSelfClient(Client):
    connect = AsyncMock()
    disconnect = AsyncMock()

    def __init__(self: "TypedSelfClient") -> None:
        pass


async def test_typed_self() -> None:
    dep = Dependencies()
    cli = dep.resolve(TypedSelfClient)

    async with dep:
        pass

    assert cli.connect.await_args_list == [call()]
    assert cli.disconnect.await_args_list == [call()]
