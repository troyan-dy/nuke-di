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


class SelfCancelling(Recorded):
    """
    Ends its disconnect() in a CancelledError of its own, as a client re-raising the cancellation of a task it awaited.
    """

    def __init__(self, pg: Postgres) -> None:
        self.pg = pg

    async def disconnect(self) -> None:
        raise asyncio.CancelledError


class Interrupted(Recorded):
    """
    Records that its disconnect() saw the cancellation and got to finish it.
    """

    def __init__(self, pg: Postgres) -> None:
        self.pg = pg

    async def disconnect(self) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # A cleanup that still needs the event loop
            await asyncio.sleep(0)
            recorder.disconnected.append("Interrupted: cancelled")
            raise


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


async def test_cancelled_disconnect_waits_for_the_layer_and_flushes() -> None:
    dep = Dependencies()
    dep.resolve(Interrupted)
    dep.resolve(Sibling)
    await dep.connect()

    task = asyncio.create_task(dep.disconnect())
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # The clients of the layer finished their cancellation before disconnect() gave up; the layer below was not reached
    assert recorder.disconnected == ["Sibling", "Interrupted: cancelled"]
    assert dep.connected is False
    assert dep.connect_clients == []
    assert dep.clients == {}

    # The next connect() starts from a fresh graph, not from the half-disconnected clients
    dep.resolve(Redis)
    async with dep:
        assert [type(client) for client in dep.connect_clients] == [Redis]
    assert recorder.disconnected == ["Sibling", "Interrupted: cancelled", "Redis"]


async def test_cancelled_rollback_flushes() -> None:
    dep = Dependencies()
    # Interrupted connects before Broken fails, so the rollback disconnects it and hangs there
    dep.resolve(Interrupted)
    dep.resolve(Broken)

    task = asyncio.create_task(dep.connect())
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert recorder.disconnected == ["Interrupted: cancelled"]
    assert dep.connected is False
    assert dep.connect_clients == []
    assert dep.clients == {}


async def test_client_cancelling_itself_does_not_stop_disconnect() -> None:
    dep = Dependencies()
    dep.resolve(SelfCancelling)
    dep.resolve(Sibling)
    await dep.connect()

    # Returns normally: the rest of the layer and the layer below are still disconnected
    await dep.disconnect()

    assert recorder.disconnected == ["Sibling", "Postgres"]
    assert dep.connected is False
    assert dep.connect_clients == []
    assert dep.clients == {}
    [timing] = [timing for timing in dep.timings if timing.name == "SelfCancelling"]
    assert timing.disconnect_outcome == "cancelled"
