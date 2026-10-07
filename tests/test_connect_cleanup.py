import asyncio

import pytest

from nuke_di import Client, ConnectError, Dependencies


class Recorder:
    def __init__(self) -> None:
        self.disconnected: list[str] = []


recorder = Recorder()


@pytest.fixture(autouse=True)
def reset_recorder() -> None:
    recorder.disconnected = []


class Recorded(Client):
    async def disconnect(self) -> None:
        recorder.disconnected.append(type(self).__name__)


class Postgres(Recorded):
    pass


class Redis(Recorded):
    pass


class Broken(Recorded):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg

    async def connect(self) -> None:
        raise RuntimeError("boom")


class Sibling(Recorded):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Hanging(Recorded):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg

    async def connect(self) -> None:
        await asyncio.sleep(10)


class Top(Recorded):
    def __init__(self, broken: Broken) -> None:
        self.broken = broken


async def test_failure_disconnects_connected_layers() -> None:
    dep = Dependencies()
    dep.resolve(Top)
    dep.resolve(Redis)

    with pytest.raises(ConnectError, match="Broken"):
        await dep.connect()

    assert sorted(recorder.disconnected) == ["Postgres", "Redis"]


async def test_failure_disconnects_connected_siblings() -> None:
    dep = Dependencies()
    # Both are in layer 1; Sibling connects before Broken fails
    dep.resolve(Sibling)
    dep.resolve(Broken)

    with pytest.raises(ConnectError):
        await dep.connect()

    assert recorder.disconnected == ["Sibling", "Postgres"]


async def test_failure_skips_cancelled_siblings() -> None:
    dep = Dependencies()
    dep.resolve(Hanging)
    dep.resolve(Broken)

    with pytest.raises(ConnectError):
        await dep.connect()

    assert recorder.disconnected == ["Postgres"]


async def test_failure_resets_container() -> None:
    dep = Dependencies()
    dep.resolve(Broken)

    with pytest.raises(ConnectError):
        await dep.connect()

    assert dep.connected is False
    assert dep.connect_clients == []
    assert dep.clients == {}


async def test_cancellation_disconnects_connected_layers() -> None:
    dep = Dependencies()
    dep.resolve(Hanging)
    dep.resolve(Redis)

    task = asyncio.create_task(dep.connect())
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert sorted(recorder.disconnected) == ["Postgres", "Redis"]
    assert dep.connected is False
    assert dep.connect_clients == []
