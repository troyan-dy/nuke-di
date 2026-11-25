import asyncio
import sys

import pytest

from nuke_di import Client, ConnectError, ConnectTimeoutError, Dependencies, InitializeDependencyError
from nuke_di._container.connect import Connect
from nuke_di._container.registry import ClientClass
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


async def _cancel_current_task() -> None:
    task = asyncio.current_task()
    assert task is not None
    task.cancel()
    await asyncio.sleep(0)


class CancelsOwnConnect(Sibling):
    async def connect(self) -> None:
        await _cancel_current_task()


class DependsOnCancellingClient(Recorded):
    def __init__(self, dependency: CancelsOwnConnect) -> None:
        self.dependency = dependency

    async def connect(self) -> None:
        pytest.fail("a dependent ran after its dependency cancelled startup")


class CancelsOwnDisconnect(Sibling):
    async def disconnect(self) -> None:
        await _cancel_current_task()


class Interrupted(Recorded):
    """
    Records that its disconnect() saw the cancellation and got to finish it.
    """

    def __init__(self, pg: Postgres) -> None:
        self.pg = pg
        self.disconnect_started = asyncio.Event()

    async def disconnect(self) -> None:
        self.disconnect_started.set()
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


async def test_failure_skips_cancelled_siblings(caplog: pytest.LogCaptureFixture) -> None:
    dep = Dependencies()
    dep.resolve(Hanging)
    dep.resolve(Broken)

    with pytest.raises(ConnectError, match="Broken"):
        await dep.connect()

    assert recorder.disconnected == ["Postgres"]
    assert "Hanging.connect() raised CancelledError" not in caplog.text


@pytest.mark.parametrize("previously_cancelled", [False, True])
async def test_self_cancelled_connect_rolls_back(previously_cancelled: bool) -> None:
    dep = Dependencies()
    dep.resolve(DependsOnCancellingClient)

    async def connect() -> None:
        if previously_cancelled:
            with pytest.raises(asyncio.CancelledError):
                await _cancel_current_task()

        async with asyncio.timeout(0.5):
            with pytest.raises(ConnectError, match="CancelsOwnConnect") as raised:
                await dep.connect()
        assert isinstance(raised.value.__cause__, asyncio.CancelledError)

    await asyncio.create_task(connect())

    assert recorder.disconnected == ["Postgres"]
    assert not dep.connected
    assert not dep.clients


async def test_pending_self_cancellation_prevents_starting_consumers(monkeypatch: pytest.MonkeyPatch) -> None:
    dep = Dependencies()
    consumer = dep.resolve(DependsOnCancellingClient)

    async def cancel_and_return() -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()

    monkeypatch.setattr(consumer.dependency, "connect", cancel_and_return)
    async with asyncio.timeout(0.5):
        with pytest.raises(ConnectError, match="CancelsOwnConnect"):
            await dep.connect()

    assert recorder.disconnected == ["CancelsOwnConnect", "Postgres"]
    assert not dep.connected


async def test_self_cancelled_disconnect_releases_dependencies() -> None:
    dep = Dependencies()
    dep.resolve(CancelsOwnDisconnect)
    await dep.connect()

    async with asyncio.timeout(0.5):
        await dep.disconnect()

    assert recorder.disconnected == ["Postgres"]
    assert not dep.connected
    assert not dep.clients


async def test_failure_resets_container() -> None:
    dep = Dependencies()
    dep.resolve(Broken)

    with pytest.raises(ConnectError):
        await dep.connect()

    assert dep.connected is False
    assert not dep.clients


@pytest.mark.parametrize(
    "error_type", [ConnectError, ConnectTimeoutError, InitializeDependencyError, SystemExit, KeyboardInterrupt]
)
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
    assert (dep.connected, dep.clients) == (False, {})
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
    assert (dep.connected, dep.clients) == (False, {})


async def _check_cancellation(expected: bool) -> None:
    if expected:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.sleep(0)
        return
    await asyncio.sleep(0)


async def _check_startup_failure(dep: Dependencies, error: BaseException) -> None:
    reported_type = ConnectError if isinstance(error, OSError) else type(error)
    with pytest.raises(reported_type) as raised:
        await dep.connect()
    assert raised.value is error or raised.value.__cause__ is error
    assert recorder.disconnected == ["Postgres"]


@pytest.fixture
def suspending_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def disconnect(client: Postgres) -> None:
        # A real suspension before releasing the resource, e.g. closing a pool.
        await asyncio.sleep(0)
        await Recorded.disconnect(client)

    monkeypatch.setattr(Postgres, "disconnect", disconnect)


@pytest.fixture
def reissuing_task_group(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.version_info >= (3, 13):
        return
    connect = Connect._connect

    async def connect_with_reissue(
        controller: Connect, clients: dict[ClientClass, Client], prior_cancels: int
    ) -> float:
        # Exercise the 3.13+ TaskGroup contract under the per-version coverage gate too.
        try:
            return await connect(controller, clients, prior_cancels)
        except BaseExceptionGroup:
            task = asyncio.current_task()
            assert task is not None and task.cancelling()
            task.uncancel()
            task.cancel()
            raise

    monkeypatch.setattr(Connect, "_connect", connect_with_reissue)


@pytest.mark.usefixtures("suspending_disconnect")
@pytest.mark.parametrize("error_type", [OSError, SystemExit])
@pytest.mark.parametrize("cancel_before_start", [False, True], ids=["concurrent", "previous"])
async def test_startup_cancellation_does_not_interrupt_rollback(
    error_type: type[BaseException],
    cancel_before_start: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dep = Dependencies()
    broken = dep.resolve(Broken)
    error = error_type("boom")

    async def fail() -> None:
        if not cancel_before_start:
            task.cancel("stop startup")
        raise error

    async def connect() -> None:
        if cancel_before_start:
            task.cancel()
            await _check_cancellation(expected=True)

        await _check_startup_failure(dep, error)
        await _check_cancellation(expected=not cancel_before_start and sys.version_info >= (3, 13))

    monkeypatch.setattr(broken, "connect", fail)
    task = asyncio.create_task(connect())
    await task
    assert (dep.connected, dep.clients) == (False, {})


@pytest.mark.usefixtures("reissuing_task_group", "suspending_disconnect")
async def test_startup_failure_preserves_reissued_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    dep = Dependencies()
    broken = dep.resolve(Broken)
    error = OSError("boom")

    async def fail() -> None:
        task.cancel()
        raise error

    async def connect() -> None:
        await _check_startup_failure(dep, error)
        await _check_cancellation(expected=True)

    monkeypatch.setattr(broken, "connect", fail)
    task = asyncio.create_task(connect())
    await task


async def test_pending_cancellation_prevents_startup() -> None:
    dep = Dependencies()
    dep.resolve(Broken)

    async def connect() -> None:
        parent = asyncio.current_task()
        assert parent is not None
        parent.cancel("pending")
        with pytest.raises(asyncio.CancelledError, match="pending"):
            await dep.connect()

    await asyncio.create_task(connect())

    assert recorder.disconnected == []
    assert (dep.connected, dep.clients) == (False, {})


@pytest.mark.usefixtures("suspending_disconnect")
async def test_new_cancellation_at_rollback_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    dep = Dependencies()
    dep.resolve(Broken)
    rollback = Connect._rollback
    cancels_before_checkpoint: list[int] = []

    async def connect() -> BaseException | None:
        task.cancel()
        await _check_cancellation(expected=True)
        try:
            await dep.connect()
        except asyncio.CancelledError as cancelled:
            return cancelled
        except ConnectError as error:
            await _check_cancellation(expected=True)
            return error
        return None

    async def cancel_at_checkpoint(
        controller: Connect,
        error: BaseException,
        parent: asyncio.Task[object] | None,
        prior_cancels: int,
    ) -> None:
        cancels_before_checkpoint.append(task.cancelling())
        asyncio.get_running_loop().call_soon(task.cancel, "stop rollback")
        await rollback(controller, error, parent, prior_cancels)

    monkeypatch.setattr(Connect, "_rollback", cancel_at_checkpoint)
    task = asyncio.create_task(connect())
    outcome = await task
    assert isinstance(outcome, asyncio.CancelledError) and outcome.args == ("stop rollback",)
    assert task.cancelling() == single(cancels_before_checkpoint) + 1
    assert not recorder.disconnected
    assert (dep.connected, dep.clients) == (False, {})


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
    assert not dep.clients


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
    assert not dep.clients

    # The next connect() starts from a fresh graph, not from the half-disconnected clients
    dep.resolve(Redis)
    async with dep:
        assert list(dep.clients) == [Redis]
    assert recorder.disconnected == ["Sibling", "Interrupted: cancelled", "Redis"]


@pytest.mark.parametrize("cancel_during_startup", [False, True])
async def test_cancelled_rollback_flushes(cancel_during_startup: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    dep = Dependencies()
    # Interrupted connects before Broken fails, so the rollback disconnects it and hangs there
    interrupted = dep.resolve(Interrupted)
    broken = dep.resolve(Broken)

    async def fail() -> None:
        task.cancel()
        raise RuntimeError("boom")

    if cancel_during_startup:
        monkeypatch.setattr(broken, "connect", fail)
    task = asyncio.create_task(dep.connect())
    await asyncio.wait_for(interrupted.disconnect_started.wait(), timeout=2)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert recorder.disconnected == ["Interrupted: cancelled"]
    assert dep.connected is False
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
    assert not dep.clients
