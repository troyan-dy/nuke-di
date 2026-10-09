import sys
from typing import Any
from unittest.mock import AsyncMock, call

import pytest

from nuke_di import (
    CircularDependencyError,
    Client,
    Dependencies,
    InitializeDependencyError,
    InvalidSignatureError,
    NotSingletonClient,
)


class InnerDeps(NotSingletonClient):
    connect: Any = AsyncMock()
    disconnect: Any = AsyncMock()


class PublicClient(Client):
    connect: Any = AsyncMock()
    disconnect: Any = AsyncMock()

    def __init__(self, inner: InnerDeps):
        self.inner = inner


class NewPublicClient(PublicClient):
    connect: Any = AsyncMock()
    disconnect: Any = AsyncMock()


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
    connect: Any = AsyncMock()
    disconnect: Any = AsyncMock()

    def __init__(self: "TypedSelfClient") -> None:
        pass


async def test_typed_self() -> None:
    dep = Dependencies()
    cli = dep.resolve(TypedSelfClient)

    async with dep:
        pass

    assert cli.connect.await_args_list == [call()]
    assert cli.disconnect.await_args_list == [call()]


def chain(n: int, name: str = "Link", bottom: type[Client] | None = None) -> list[type[Client]]:
    """
    `n` clients, each taking the one before it, the first one taking `bottom` if given: a tree `n` layers deep.
    """
    links: list[type[Client]] = []
    below = bottom
    for number in range(n):
        namespace: dict[str, Any] = {}
        if below is not None:

            def init(self: Any, below: Any) -> None:
                self.below = below

            init.__annotations__ = {"below": below, "return": None}
            namespace["__init__"] = init
        below = type(f"{name}{number}", (Client,), namespace)
        links.append(below)
    return links


def test_chain_deeper_than_the_recursion_limit() -> None:
    links = chain(2000)
    assert sys.getrecursionlimit() < len(links)

    dep = Dependencies()
    root = dep.resolve(links[-1])

    built: list[Any] = [root]
    while hasattr(built[-1], "below"):
        built.append(built[-1].below)
    assert [type(client) for client in reversed(built)] == links
    assert dep.connect_clients == built[::-1]
    assert [node.layer for node in dep.graph().nodes] == list(range(2000))


class Unbuildable(Client):
    def __init__(self) -> None:
        raise ValueError("no pool")


class Unsigned(Client):
    def __init__(self, pool: int) -> None:
        self.pool = pool


class Loop(Client):
    def __init__(self, link: "LoopLink") -> None:
        self.link = link


class LoopLink(Client):
    def __init__(self, loop: Loop) -> None:
        self.loop = loop


@pytest.mark.parametrize(
    ("bottom", "error", "message"),
    [
        (Unbuildable, InitializeDependencyError, "Unbuildable.__init__ raised ValueError (resolving {}): no pool"),
        (
            Unsigned,
            InvalidSignatureError,
            'Argument "pool" of "Unsigned.__init__" is int, which is not a client (resolving {})',
        ),
        (Loop, CircularDependencyError, "Circular dependency: {} -> LoopLink -> Loop"),
    ],
)
def test_failure_deep_in_a_chain_closes_every_frame(
    bottom: type[Client], error: type[BaseException], message: str
) -> None:
    links = chain(1500, bottom=bottom)
    dep = Dependencies()

    with pytest.raises(error) as info:
        dep.resolve(links[-1])

    path = " -> ".join([*(link.__name__ for link in reversed(links)), bottom.__name__])
    assert str(info.value) == message.format(path)
    # Nothing of the chain was built, and no frame of it is left on the path
    assert dep.connect_clients == []
    assert dep.clients == {}
    assert dep._resolving == []
    assert dep._resolving_set == set()

    # The next tree resolves from scratch: no stale cycle, and an error names its own path
    healthy = chain(1500, name="Healthy")
    assert isinstance(dep.resolve(healthy[-1]), healthy[-1])
    assert len(dep.connect_clients) == 1500
    assert dep._resolving == []
    with pytest.raises(CircularDependencyError, match=r"^Circular dependency: Loop -> LoopLink -> Loop$"):
        dep.resolve(Loop)
