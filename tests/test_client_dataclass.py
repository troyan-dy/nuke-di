from dataclasses import FrozenInstanceError, is_dataclass

import pytest

from nuke_di import Client, Dependencies, NotSingletonClient, client_dataclass


@client_dataclass
class Plain:
    """A plain class, a client through client_dataclass."""


class Singleton(Client):
    """A client the container builds once."""


class Fresh(NotSingletonClient):
    """A client the container builds for every argument that takes it."""


@client_dataclass(frozen=True)
class MyClient(Client):
    singleton: Singleton
    fresh: Fresh
    plain: Plain

    def plain_client(self) -> Plain:
        return self.plain

    def singleton_client(self) -> Singleton:
        return self.singleton

    def fresh_client(self) -> Fresh:
        return self.fresh


@pytest.fixture
def my_client() -> MyClient:
    di = Dependencies()
    return di.resolve(MyClient)


def test_methods(my_client: MyClient) -> None:
    assert my_client.singleton_client().__class__ is Singleton
    assert my_client.fresh_client().__class__ is Fresh
    assert my_client.plain_client().__class__ is Plain


def test_is_dataclass(my_client: MyClient) -> None:
    assert is_dataclass(my_client)


def test_dataclass_init(my_client: MyClient) -> None:
    assert my_client.singleton.__class__ is Singleton
    assert my_client.fresh.__class__ is Fresh
    assert my_client.plain.__class__ is Plain


def test_frozen(my_client: MyClient) -> None:
    with pytest.raises(FrozenInstanceError):
        my_client.plain = 9090  # type: ignore[assignment,misc]


def test_client_inheritance(my_client: MyClient) -> None:
    assert getattr(my_client, "connect", False)


@client_dataclass
class AlreadyClient(Client):
    singleton: Singleton


def test_already_client_keeps_class() -> None:
    assert AlreadyClient.__bases__ == (Client,)
    client: AlreadyClient = Dependencies().resolve(AlreadyClient)
    assert client.singleton.__class__ is Singleton
    assert is_dataclass(client)
