from dataclasses import FrozenInstanceError, is_dataclass

import pytest

from nuke_di import Client, Dependencies, NotSingletonClient, client_dataclass


@client_dataclass
class A:
    pass


class B(Client):
    pass


class C(NotSingletonClient):
    pass


@client_dataclass(frozen=True)
class MyClient:
    b: B
    c: C
    a: A

    def get_a(self) -> A:
        return self.a

    def get_b(self) -> B:
        return self.b

    def get_c(self) -> C:
        return self.c


@pytest.fixture
def my_client() -> MyClient:
    di = Dependencies()
    return di.resolve(MyClient)  # type: ignore[type-var]


def test_methods(my_client: MyClient) -> None:
    assert type(my_client.get_b()) is B
    assert type(my_client.get_c()) is C
    assert type(my_client.get_a()) is A


def test_is_dataclass(my_client: MyClient) -> None:
    assert is_dataclass(my_client)


def test_dataclass_init(my_client: MyClient) -> None:
    assert type(my_client.b) is B
    assert type(my_client.c) is C
    assert type(my_client.a) is A


def test_frozen(my_client: MyClient) -> None:
    with pytest.raises(FrozenInstanceError):
        my_client.a = 9090  # type: ignore[assignment,misc]


def test_client_inheritance(my_client: MyClient) -> None:
    assert getattr(my_client, "connect", False)
