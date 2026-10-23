import sys
from typing import Any, TypeAlias
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


def awaits(cli: PublicClient, new_cli: NewPublicClient) -> dict[str, list[Any]]:
    """
    The awaits of every connect and disconnect mock of the two trees, keyed by the mock's place.
    """
    return {
        "cli.connect": cli.connect.await_args_list,
        "cli.disconnect": cli.disconnect.await_args_list,
        "new_cli.connect": new_cli.connect.await_args_list,
        "new_cli.disconnect": new_cli.disconnect.await_args_list,
        "cli.inner.connect": cli.inner.connect.await_args_list,
        "cli.inner.disconnect": cli.inner.disconnect.await_args_list,
    }


async def test_connecting() -> None:
    dep = Dependencies()
    cli = dep.resolve(PublicClient)
    new_cli = dep.resolve(NewPublicClient)

    assert isinstance(cli, PublicClient) and isinstance(new_cli, NewPublicClient)
    # resolving, not connecting
    assert awaits(cli, new_cli) == {
        "cli.connect": [],
        "cli.disconnect": [],
        "new_cli.connect": [],
        "new_cli.disconnect": [],
        "cli.inner.connect": [],
        "cli.inner.disconnect": [],
    }

    async with dep:
        # connecting
        assert awaits(cli, new_cli) == {
            "cli.connect": [call()],
            "cli.disconnect": [],
            "new_cli.connect": [call()],
            "new_cli.disconnect": [],
            "cli.inner.connect": [call(), call()],
            "cli.inner.disconnect": [],
        }

        assert cli.inner is not new_cli.inner

    # disconnecting
    assert awaits(cli, new_cli) == {
        "cli.connect": [call()],
        "cli.disconnect": [call()],
        "new_cli.connect": [call()],
        "new_cli.disconnect": [call()],
        "cli.inner.connect": [call(), call()],
        "cli.inner.disconnect": [call(), call()],
    }


class TypedSelfClient(Client):
    connect: Any = AsyncMock()
    disconnect: Any = AsyncMock()

    def __init__(self: "TypedSelfClient") -> None: ...


async def test_typed_self() -> None:
    dep = Dependencies()
    cli = dep.resolve(TypedSelfClient)

    async with dep:
        ...

    assert cli.connect.await_args_list == [call()]
    assert cli.disconnect.await_args_list == [call()]


def chain(
    length: int,
    name: str = "Link",
    bottom: type[Client] | None = None,
) -> list[type[Client]]:
    """
    `length` clients, each taking the one before it, the first one taking `bottom` if given: a tree `length` clients
    deep.
    """
    links: list[type[Client]] = []
    below = bottom
    for number in range(length):
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
    assert dep.connect_clients == list(reversed(built))
    assert [node.client_class for node in dep.graph().nodes] == links


ResolvePath: TypeAlias = list[type[NotSingletonClient]]


def arrows(path: list[type[Client]]) -> str:
    """
    The names of the classes on `path`, joined the way an error message shows a resolve path.
    """
    return " -> ".join(client_class.__name__ for client_class in path)


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

    with pytest.raises(error) as excinfo:
        dep.resolve(links[-1])

    path = arrows([*reversed(links), bottom])
    assert str(excinfo.value) == message.format(path)
    # Nothing of the chain was built, and no frame of it is left on the path
    assert (
        dep.connect_clients,
        dep.clients,
        dep._resolving,
        dep._resolving_set,
    ) == ([], {}, [], set())

    # The next tree resolves from scratch: no stale cycle, and an error names its own path
    healthy = chain(1500, name="Healthy")
    assert isinstance(dep.resolve(healthy[-1]), healthy[-1])
    assert (len(dep.connect_clients), dep._resolving) == (1500, [])
    with pytest.raises(CircularDependencyError, match=r"^Circular dependency: Loop -> LoopLink -> Loop$"):
        dep.resolve(Loop)  # type: ignore[nuke-di]


def test_failed_resolve_inside_an_init_closes_only_its_own_frames() -> None:
    """
    A client's `__init__` that resolves on its own and swallows the failure: the failed tree's frames come off the
    path, the frames of the outer resolve stay on it, and the outer resolve completes.
    """
    dep = Dependencies()
    failing = chain(3, name="Bad", bottom=Unbuildable)

    class Leaf(Client):
        """The one client of the tree that resolves without failing."""

    class Swallower(Client):
        def __init__(self, leaf: Leaf) -> None:
            self.errors: list[str] = []
            self.paths: list[ResolvePath] = []
            for client_class in (Loop, failing[-1]):
                try:
                    dep.resolve(client_class)
                except BaseException as exc:
                    self.errors.append(str(exc))
                self.paths.append(list(dep._resolving))

    class Top(Client):
        def __init__(self, swallower: Swallower) -> None:
            self.swallower = swallower

    top = dep.resolve(Top)

    assert top.swallower.errors == [
        "Circular dependency: Top -> Loop -> LoopLink -> Loop",
        "Unbuildable.__init__ raised ValueError (resolving Top -> Bad2 -> Bad1 -> Bad0 -> Unbuildable): no pool",
    ]
    # The `__init__` runs with its consumer on the path, and each failure left it there
    assert top.swallower.paths == [[Top], [Top]]
    assert (dep._resolving, dep._resolving_set) == ([], set())
    assert [type(client) for client in dep.connect_clients] == [Leaf, Swallower, Top]
    assert dep.clients[Top] is top

    with pytest.raises(CircularDependencyError, match=r"^Circular dependency: Loop -> LoopLink -> Loop$"):
        dep.resolve(Loop)  # type: ignore[nuke-di]
