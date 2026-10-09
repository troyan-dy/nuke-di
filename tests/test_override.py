from unittest.mock import NonCallableMock

import pytest

from nuke_di import DI, Client, ConnectError, Dependencies


class Database(Client):
    async def fetch(self) -> str:
        return "real"


class Clock(Client):
    pass


class Users(Client):
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock


class FakeDatabase(Database):
    async def fetch(self) -> str:
        return "fake"


async def test_override_replaces_client_inside_block() -> None:
    dep = Dependencies()
    fake = FakeDatabase()

    with dep.override(Database, fake) as db:
        users = dep.resolve(Users)
        async with dep:
            assert await users.db.fetch() == "fake"

    assert db is fake


def test_override_without_replacement_creates_autospec() -> None:
    dep = Dependencies()

    with dep.override(Database) as db:
        assert isinstance(db, NonCallableMock)
        assert isinstance(db, Database)
        assert dep.resolve(Users).db is db


def test_override_flushes_container_on_exit() -> None:
    dep = Dependencies()

    with dep.override(Database):
        dep.resolve(Users)

    assert dep.clients == {}
    assert isinstance(dep.resolve(Users).db, Database)


def test_override_keeps_enclosing_overrides() -> None:
    dep = Dependencies()

    with dep.override(Database) as db:
        with dep.override(Clock) as clock:
            users = dep.resolve(Users)
            assert (users.db, users.clock) == (db, clock)

        users = dep.resolve(Users)
        assert users.db is db
        assert isinstance(users.clock, Clock)


def test_override_cleans_up_when_block_raises() -> None:
    dep = Dependencies()

    with pytest.raises(ValueError, match="boom"), dep.override(Database):
        dep.resolve(Users)
        raise ValueError("boom")

    assert dep.clients == {}


def test_override_after_resolve_raises() -> None:
    dep = Dependencies()
    dep.resolve(Users)

    with pytest.raises(ConnectError, match="needs a container without resolved clients"), dep.override(Database):
        pass


async def test_override_exit_while_connected_raises() -> None:
    dep = Dependencies()

    with pytest.raises(ConnectError, match=r"override\(Database\) exited while the container is connected"):
        with dep.override(Database):
            dep.resolve(Users)
            await dep.connect()

    await dep.disconnect()


async def test_override_global_container() -> None:
    with DI.override(Database, FakeDatabase()):
        users = DI.resolve(Users)
        async with DI:
            assert await users.db.fetch() == "fake"

    assert DI.clients == {}


def test_override_drops_mocks_on_exit() -> None:
    dep = Dependencies()
    clock = dep.mock(Clock)

    with dep.override(Database):
        pass

    assert dep.resolve(Users).clock is not clock


async def test_override_survives_disconnect_inside_block() -> None:
    dep = Dependencies()

    with dep.override(Database, FakeDatabase()):
        for _ in range(2):
            users = dep.resolve(Users)
            async with dep:
                assert await users.db.fetch() == "fake"


def test_override_needs_container_without_resolved_clients() -> None:
    dep = Dependencies()
    dep.resolve(Clock)

    expected = r"override\(Database\) needs a container without resolved clients, found: Clock"
    with pytest.raises(ConnectError, match=expected), dep.override(Database):
        pass

    assert Clock in dep.clients


def test_nested_override_after_resolve_raises() -> None:
    dep = Dependencies()

    with dep.override(Database):
        dep.resolve(Users)
        with pytest.raises(ConnectError, match="found: Clock, Users"), dep.override(Clock):
            pass


def test_override_of_replaced_client_raises() -> None:
    dep = Dependencies()
    dep.mock(Database)

    with pytest.raises(ConnectError, match="Database already has a replacement"), dep.override(Database):
        pass


async def test_override_keeps_exception_while_connected() -> None:
    dep = Dependencies()

    with pytest.raises(ValueError, match="boom"), dep.override(Database):
        dep.resolve(Users)
        await dep.connect()
        raise ValueError("boom")

    assert dep.connected
    await dep.disconnect()
    assert dep.clients == {}


def test_override_when_connected_raises() -> None:
    dep = Dependencies()
    dep.connected = True

    with pytest.raises(ConnectError, match="already connected"), dep.override(Database):
        pass
