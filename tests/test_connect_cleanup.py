import asyncio

import pytest

from nuke_di import Client, ConnectError, Dependencies
from tests.sequences import single


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
    """A recorded client without dependencies."""


class Redis(Recorded):
    """A second recorded client without dependencies."""


class Broken(Recorded):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg

    async def connect(self) -> None:
        raise RuntimeError("boom")


class CompetingFailure(Broken):
    """An independent failure that can reach the task group before Broken."""


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


async def test_failure_disconnects_connected_clients() -> None:
    dep = Dependencies()
    dep.resolve(Top)
    dep.resolve(Redis)

    with pytest.raises(ConnectError, match="Broken"):
        await dep.connect()

    assert sorted(recorder.disconnected) == ["Postgres", "Redis"]


async def test_failure_disconnects_connected_siblings() -> None:
    dep = Dependencies()
    # Both wait for Postgres only; Sibling connects before Broken fails
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
    assert not dep.connect_clients
    assert not dep.clients


@pytest.mark.parametrize("error_type", [ConnectError, SystemExit, KeyboardInterrupt])
@pytest.mark.parametrize("with_competing_failure", [False, True])
def test_base_exception_rolls_back_connect(
    error_type: type[BaseException],
    with_competing_failure: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dep = Dependencies()
    dep.resolve(Hanging)
    if with_competing_failure:
        dep.resolve(CompetingFailure)
    error = error_type("stopped")

    async def fail() -> None:
        try:
            int("invalid configuration")
        except ValueError as cause:
            raise error from cause

    async def connect() -> None:
        with pytest.raises(error_type) as raised:
            await dep.connect()
        cause = error.__cause__
        assert raised.value is error and isinstance(cause, ValueError) and error.__context__ is cause

    monkeypatch.setattr(dep.resolve(Top).broken, "connect", fail)
    try:
        asyncio.run(connect())
    except (SystemExit, KeyboardInterrupt) as escaped:
        raise AssertionError("A client's exception escaped the calling coroutine") from escaped

    assert recorder.disconnected == ["Postgres"]
    assert (dep.connected, dep.connect_clients, dep.clients) == (False, [], {})
    outcomes = {timing.name: timing.connect_outcome for timing in dep.timings}
    expected = {"Postgres": "ok", "Broken": "failed", "Hanging": "cancelled", "Top": None}
    if with_competing_failure:
        expected["CompetingFailure"] = "failed"
    assert outcomes == expected
    assert f"Broken.connect() raised {error_type.__name__}: stopped" in caplog.text


async def test_base_exception_group_rolls_back_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    dep = Dependencies()
    broken = dep.resolve(Broken)
    error = BaseException("stopped")

    async def fail() -> None:
        raise error

    monkeypatch.setattr(broken, "connect", fail)
    with pytest.raises(BaseExceptionGroup) as raised:
        await dep.connect()

    assert raised.value.exceptions == (error,)
    assert recorder.disconnected == ["Postgres"]
    assert (dep.connected, dep.connect_clients, dep.clients) == (False, [], {})
    timing = single(other for other in dep.timings if other.name == "Broken")
    assert timing.connect_outcome == "failed"


async def test_cancellation_disconnects_connected_clients() -> None:
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
    assert not dep.connect_clients


async def test_cancelled_disconnect_waits_for_the_clients_disconnecting_and_flushes() -> None:
    dep = Dependencies()
    dep.resolve(Interrupted)
    dep.resolve(Sibling)
    await dep.connect()

    task = asyncio.create_task(dep.disconnect())
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # The clients disconnecting finished their cancellation before disconnect() gave up; Postgres, waiting for them,
    # was not reached
    assert recorder.disconnected == ["Sibling", "Interrupted: cancelled"]
    assert dep.connected is False
    assert (dep.connect_clients, dep.clients) == ([], {})

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
    assert not dep.connect_clients
    assert not dep.clients


async def test_client_cancelling_itself_does_not_stop_disconnect() -> None:
    dep = Dependencies()
    dep.resolve(SelfCancelling)
    dep.resolve(Sibling)
    await dep.connect()

    # Returns normally: Sibling and Postgres, which waits for it, are still disconnected
    await dep.disconnect()

    assert recorder.disconnected == ["Sibling", "Postgres"]
    assert dep.connected is False
    assert not dep.connect_clients
    assert not dep.clients
    timing = single(other for other in dep.timings if other.name == "SelfCancelling")
    assert timing.disconnect_outcome == "cancelled"
