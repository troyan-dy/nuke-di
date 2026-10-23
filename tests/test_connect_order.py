import asyncio
import logging
import re
from collections.abc import Iterator

import pytest

from nuke_di import Client, ConnectError, Dependencies, DependenciesSettings
from tests.sequences import repeated, single
from tests.test_resolve import chain

# The bound on a disconnect() the deadline cuts short: well under the timeouts that would run in turn without it
WITHIN_DEADLINE = 0.5


class Probe:
    """
    Records the lifecycle calls of the clients below and the peak number of calls running at once.
    """

    def __init__(self) -> None:
        self.events: list[str] = []
        self.running = 0
        self.peak = 0

    async def run(self, event: str) -> None:
        self.events.append(f"{event}:start")
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(0.01)
        except BaseException:
            self.running -= 1
            raise
        self.running -= 1
        self.events.append(f"{event}:end")

    def before(self, first: str, second: str) -> bool:
        return self.events.index(f"{first}:end") < self.events.index(f"{second}:start")


probe = Probe()


@pytest.fixture(autouse=True)
def reset_probe() -> Iterator[None]:
    probe.__init__()  # type: ignore[misc]
    yield


class Tracked(Client):
    async def connect(self) -> None:
        await probe.run(f"connect:{type(self).__name__}")

    async def disconnect(self) -> None:
        await probe.run(f"disconnect:{type(self).__name__}")


class Postgres(Tracked):
    """A database, the dependency the other tracked clients share."""


class Redis(Tracked):
    """A cache that depends on nothing, independent of Postgres."""


class Payments(Tracked):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Checkout(Tracked):
    def __init__(self, pg: Postgres, payments: Payments) -> None:
        self.pg = pg
        self.payments = payments


async def test_independent_clients_connect_concurrently() -> None:
    dep = Dependencies()
    dep.resolve(Postgres)
    dep.resolve(Redis)

    async with dep:
        assert probe.peak == 2


async def test_dependency_connects_before_consumer() -> None:
    dep = Dependencies()
    # diamond: Checkout depends on Postgres both directly and through Payments
    dep.resolve(Checkout)

    async with dep:
        assert probe.before("connect:Postgres", "connect:Payments")
        assert probe.before("connect:Payments", "connect:Checkout")


async def test_consumer_disconnects_before_dependency() -> None:
    dep = Dependencies()
    dep.resolve(Checkout)

    async with dep:
        ...

    assert probe.before("disconnect:Checkout", "disconnect:Payments")
    assert probe.before("disconnect:Payments", "disconnect:Postgres")


async def test_independent_clients_disconnect_concurrently() -> None:
    dep = Dependencies()
    dep.resolve(Postgres)
    dep.resolve(Redis)

    async with dep:
        probe.peak = 0

    assert probe.peak == 2


class Mocked(Client):
    """A client the test replaces with a mock, which has nothing to connect."""


class UsesMock(Tracked):
    def __init__(self, mocked: Mocked) -> None:
        self.mocked = mocked


async def test_consumer_of_a_mock_waits_for_nothing() -> None:
    dep = Dependencies()
    dep.mock(Mocked)
    dep.resolve(UsesMock)
    dep.resolve(Postgres)

    async with dep:
        assert probe.peak == 2


class Broken(Client):
    async def connect(self) -> None:
        raise RuntimeError("boom")


class Slow(Client):
    cancelled = False

    async def connect(self) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            Slow.cancelled = True
            raise


class AfterSlow(Tracked):
    def __init__(self, slow: Slow) -> None:
        self.slow = slow


async def test_failure_cancels_the_clients_still_connecting() -> None:
    dep = Dependencies()
    dep.resolve(Broken)
    dep.resolve(AfterSlow)

    with pytest.raises(ConnectError, match="Broken") as exc_info:
        await dep.connect()

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert Slow.cancelled
    # its consumer never starts
    assert not probe.events


async def test_concurrency_limit() -> None:
    dep = Dependencies(settings=DependenciesSettings(connect_concurrency=1))
    dep.resolve(Postgres)
    dep.resolve(Redis)

    async with dep:
        assert probe.peak == 1
        probe.peak = 0

    assert probe.peak == 1


def test_concurrency_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECT_CONCURRENCY", "3")

    assert DependenciesSettings().connect_concurrency == 3


def test_concurrency_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONNECT_CONCURRENCY", raising=False)

    assert DependenciesSettings().connect_concurrency == 0


def test_negative_concurrency() -> None:
    with pytest.raises(ValueError, match="connect_concurrency"):
        DependenciesSettings(connect_concurrency=-1)


async def test_concurrency_limit_is_not_held_while_waiting_for_dependencies() -> None:
    # A client waiting for its dependencies with the only slot would never let them connect
    dep = Dependencies(settings=DependenciesSettings(connect_concurrency=1))
    dep.resolve(Checkout)

    async with asyncio.timeout(1):
        async with dep:
            assert probe.before("connect:Postgres", "connect:Payments")


class Signals:
    """
    Couples clients of unrelated branches, so a barrier between them deadlocks instead of passing by luck.
    """

    def __init__(self) -> None:
        self.consumer_connected = asyncio.Event()
        self.kafka_disconnected = asyncio.Event()


signals = Signals()


@pytest.fixture(autouse=True)
def reset_signals() -> None:
    signals.__init__()  # type: ignore[misc]


class Database(Client):
    async def connect(self) -> None:
        # Slow: connects only after Consumer, which does not need it
        await signals.consumer_connected.wait()


class Kafka(Client):
    async def disconnect(self) -> None:
        signals.kafka_disconnected.set()


class Repository(Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def disconnect(self) -> None:
        # Slow: disconnects only after Kafka, which does not wait for it
        await signals.kafka_disconnected.wait()


class Consumer(Client):
    def __init__(self, kafka: Kafka) -> None:
        self.kafka = kafka

    async def connect(self) -> None:
        signals.consumer_connected.set()


async def test_client_connects_without_waiting_for_an_unrelated_slow_one() -> None:
    dep = Dependencies()
    dep.resolve(Repository)
    dep.resolve(Consumer)

    async with asyncio.timeout(1):
        await dep.connect()
    await dep.disconnect()


async def test_client_disconnects_without_waiting_for_an_unrelated_slow_one() -> None:
    dep = Dependencies()
    dep.resolve(Repository)
    dep.resolve(Consumer)
    signals.consumer_connected.set()
    await dep.connect()

    async with asyncio.timeout(1):
        await dep.disconnect()


class SlowConsumer(Client):
    def __init__(self, kafka: Kafka) -> None:
        self.kafka = kafka

    async def connect(self) -> None:
        await asyncio.sleep(10)


class FailsLater(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.01)
        raise RuntimeError("boom")


async def test_failure_cancels_a_client_connecting_in_another_branch() -> None:
    dep = Dependencies()
    dep.resolve(SlowConsumer)
    dep.resolve(FailsLater)

    with pytest.raises(ConnectError, match="FailsLater"):
        await dep.connect()

    timings = {timing.name: timing for timing in dep.timings}
    assert timings["Kafka"].connect_outcome == "ok"
    assert timings["Kafka"].disconnect_outcome == "ok"
    assert timings["SlowConsumer"].connect_outcome == "cancelled"
    assert timings["SlowConsumer"].disconnect_outcome is None


class Blocked(Client):
    def __init__(self, broken: Broken) -> None:
        self.broken = broken


async def test_consumer_of_a_failed_client_never_starts() -> None:
    dep = Dependencies()
    dep.resolve(Blocked)

    with pytest.raises(ConnectError, match="Broken"):
        await dep.connect()

    timing = single(timing for timing in dep.timings if timing.name == "Blocked")
    assert timing.connect is None
    assert timing.connect_outcome is None


class FailsToDisconnect(Tracked):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg

    async def disconnect(self) -> None:
        await probe.run("disconnect:FailsToDisconnect")
        raise RuntimeError("boom")


async def test_failed_disconnect_still_releases_its_dependencies() -> None:
    dep = Dependencies()
    dep.resolve(FailsToDisconnect)

    async with dep:
        ...

    assert probe.before("disconnect:FailsToDisconnect", "disconnect:Postgres")


async def test_long_chain_connects_and_disconnects() -> None:
    dep = Dependencies()
    dep.resolve(chain(2000)[-1])

    async with dep:
        assert [timing.connect_outcome for timing in dep.timings] == repeated(["ok"], 2000)

    assert [timing.disconnect_outcome for timing in dep.timings] == repeated(["ok"], 2000)


async def test_progress_in_the_client_records(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Checkout)

    async with dep:
        ...

    messages = [record.getMessage() for record in caplog.records if " client " in record.getMessage()]
    # The durations differ from run to run: a record that has one is compared up to its " in 0.0"
    prefixes = [re.sub(r" in 0\.0.*", " in 0.0", message) for message in messages]
    assert prefixes == [
        "Connecting client Postgres (0/3 connected)",
        "Connected client Postgres in 0.0",
        "Connecting client Payments (1/3 connected)",
        "Connected client Payments in 0.0",
        "Connecting client Checkout (2/3 connected)",
        "Connected client Checkout in 0.0",
        "Disconnecting client Checkout (0/3 disconnected)",
        "Disconnected client Checkout in 0.0",
        "Disconnecting client Payments (1/3 disconnected)",
        "Disconnected client Payments in 0.0",
        "Disconnecting client Postgres (2/3 disconnected)",
        "Disconnected client Postgres in 0.0",
    ]
    assert messages[1].endswith("s (1/3 connected)")
    assert messages[5].endswith("s (3/3 connected)")
    assert messages[11].endswith("s (3/3 disconnected)")


class CancelsItself(Client):
    """
    Ends its connect() in a CancelledError of its own, as a client re-raising the cancellation of a task it awaited.
    """

    async def connect(self) -> None:
        raise asyncio.CancelledError


class AfterCancelled(Tracked):
    def __init__(self, dependency: CancelsItself) -> None:
        self.dependency = dependency


async def test_client_cancelling_its_own_connect_fails_the_connect() -> None:
    # Its consumers would wait for it forever
    dep = Dependencies()
    dep.resolve(AfterCancelled)

    async with asyncio.timeout(1):
        with pytest.raises(ConnectError, match=r"^CancelsItself\.connect\(\) raised CancelledError$"):
            await dep.connect()

    assert not probe.events
    timing = single(timing for timing in dep.timings if timing.name == "CancelsItself")
    # The culprit, told apart from the clients cancelled because of it
    assert timing.connect_outcome == "failed"


class HangsOnDisconnect(Tracked):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg

    async def disconnect(self) -> None:
        await probe.run("disconnect:HangsOnDisconnect")
        await asyncio.sleep(10)


async def test_timed_out_disconnect_still_releases_its_dependencies() -> None:
    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=0.05))
    dep.resolve(HangsOnDisconnect)
    await dep.connect()

    async with asyncio.timeout(1):
        await dep.disconnect()

    assert probe.before("disconnect:HangsOnDisconnect", "disconnect:Postgres")
    timing = single(timing for timing in dep.timings if timing.name == "HangsOnDisconnect")
    assert timing.disconnect_outcome == "timed_out"


class HangingBottom(Tracked):
    async def disconnect(self) -> None:
        await probe.run("disconnect:HangingBottom")
        await asyncio.sleep(10)


class HangingMiddle(Tracked):
    def __init__(self, bottom: HangingBottom) -> None:
        self.bottom = bottom

    async def disconnect(self) -> None:
        await probe.run("disconnect:HangingMiddle")
        await asyncio.sleep(10)


class HangingTop(Tracked):
    def __init__(self, middle: HangingMiddle) -> None:
        self.middle = middle

    async def disconnect(self) -> None:
        await probe.run("disconnect:HangingTop")
        await asyncio.sleep(10)


async def test_deadline_bounds_a_chain_of_hung_disconnects(caplog: pytest.LogCaptureFixture) -> None:
    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=0.2, disconnect_deadline=0.3))
    dep.resolve(HangingTop)
    await dep.connect()

    loop = asyncio.get_running_loop()
    started = loop.time()
    with caplog.at_level(logging.ERROR, logger="nuke_di.core"):
        await dep.disconnect()

    # Not 0.6: the timeout of each of the three clients in turn
    assert loop.time() - started < WITHIN_DEADLINE
    assert {timing.name: timing.disconnect_outcome for timing in dep.timings} == {
        "HangingTop": "timed_out",
        "HangingMiddle": "timed_out",
        "HangingBottom": None,
    }
    assert "disconnect:HangingBottom:start" not in probe.events
    errors = [
        "HangingTop did not disconnect within 0.2s (DISCONNECT_TIMEOUT_SECONDS)",
        "HangingMiddle did not disconnect before DISCONNECT_DEADLINE_SECONDS ran out (0.3s)",
        "Skipped disconnecting 1 client after DISCONNECT_DEADLINE_SECONDS (0.3s): HangingBottom",
    ]
    missing = [error for error in errors if error not in caplog.text]
    assert not missing


async def test_zero_deadline_skips_every_client(caplog: pytest.LogCaptureFixture) -> None:
    dep = Dependencies(settings=DependenciesSettings(disconnect_deadline=0))
    dep.resolve(Checkout)
    await dep.connect()

    with caplog.at_level(logging.ERROR, logger="nuke_di.core"):
        await dep.disconnect()

    assert not any(event.startswith("disconnect:") for event in probe.events)
    assert {timing.disconnect_outcome for timing in dep.timings} == {None}
    assert {timing.disconnect for timing in dep.timings} == {None}
    record = single(record for record in caplog.records if "Skipped" in record.getMessage())
    assert (record.levelno, record.getMessage()) == (
        logging.ERROR,
        "Skipped disconnecting 3 clients after DISCONNECT_DEADLINE_SECONDS (0s): Checkout, Payments, Postgres",
    )
    assert dep.connected is False

    # Nothing is kept for the next cycle
    await dep.connect()
    await dep.disconnect()


class HangsAlone(Tracked):
    async def disconnect(self) -> None:
        await probe.run(f"disconnect:{type(self).__name__}")
        await asyncio.sleep(10)


class AlsoHangsAlone(HangsAlone):
    """A second client that hangs on disconnect, independent of the first."""


async def test_client_waiting_for_the_concurrency_slot_at_the_deadline_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dep = Dependencies(
        settings=DependenciesSettings(disconnect_timeout=10, disconnect_deadline=0.05, connect_concurrency=1)
    )
    dep.resolve(HangsAlone)
    dep.resolve(AlsoHangsAlone)
    await dep.connect()

    with caplog.at_level(logging.ERROR, logger="nuke_di.core"):
        async with asyncio.timeout(1):
            await dep.disconnect()

    outcomes = sorted(str(timing.disconnect_outcome) for timing in dep.timings)
    assert outcomes == ["None", "timed_out"]
    skipped = single(timing.name for timing in dep.timings if timing.disconnect_outcome is None)
    assert f"Skipped disconnecting 1 client after DISCONNECT_DEADLINE_SECONDS (0.05s): {skipped}" in caplog.text
    assert f"disconnect:{skipped}:start" not in probe.events


async def test_deadline_bounds_the_rollback_of_a_failed_connect() -> None:
    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=10, disconnect_deadline=0.05))
    dep.resolve(HangsAlone)
    dep.resolve(FailsLater)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(ConnectError, match="FailsLater"):
        await dep.connect()

    assert loop.time() - started < WITHIN_DEADLINE
    timings = {timing.name: timing for timing in dep.timings}
    assert timings["HangsAlone"].disconnect_outcome == "timed_out"


async def test_deadline_shorter_than_the_timeout_names_the_deadline(caplog: pytest.LogCaptureFixture) -> None:
    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=10, disconnect_deadline=0.05))
    dep.resolve(HangsOnDisconnect)
    await dep.connect()

    with caplog.at_level(logging.ERROR, logger="nuke_di.core"):
        async with asyncio.timeout(1):
            await dep.disconnect()

    timing = single(timing for timing in dep.timings if timing.name == "HangsOnDisconnect")
    assert timing.disconnect_outcome == "timed_out"
    assert "HangsOnDisconnect did not disconnect before DISCONNECT_DEADLINE_SECONDS ran out (0.05s)" in caplog.text
    assert "DISCONNECT_TIMEOUT_SECONDS" not in caplog.text


async def test_deadline_not_reached_changes_nothing(caplog: pytest.LogCaptureFixture) -> None:
    dep = Dependencies(settings=DependenciesSettings(disconnect_deadline=1))
    dep.resolve(Checkout)
    await dep.connect()

    with caplog.at_level(logging.DEBUG, logger="nuke_di"):
        await dep.disconnect()

    assert {timing.disconnect_outcome for timing in dep.timings} == {"ok"}
    assert probe.before("disconnect:Checkout", "disconnect:Payments")
    assert probe.before("disconnect:Payments", "disconnect:Postgres")
    assert "DISCONNECT_DEADLINE_SECONDS" not in caplog.text


async def test_infinite_deadline_leaves_the_timeout_alone(caplog: pytest.LogCaptureFixture) -> None:
    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=0.05, disconnect_deadline=float("inf")))
    dep.resolve(HangsOnDisconnect)
    await dep.connect()

    with caplog.at_level(logging.ERROR, logger="nuke_di.core"):
        async with asyncio.timeout(1):
            await dep.disconnect()

    assert {timing.disconnect_outcome for timing in dep.timings} == {"timed_out", "ok"}
    assert "HangsOnDisconnect did not disconnect within 0.05s (DISCONNECT_TIMEOUT_SECONDS)" in caplog.text
    assert "DISCONNECT_DEADLINE_SECONDS" not in caplog.text


async def test_deadline_longer_than_the_chain_of_timeouts_cuts_nothing(caplog: pytest.LogCaptureFixture) -> None:
    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=0.05, disconnect_deadline=1))
    dep.resolve(HangingTop)
    await dep.connect()

    with caplog.at_level(logging.ERROR, logger="nuke_di.core"):
        async with asyncio.timeout(1):
            await dep.disconnect()

    # Each client timed out on its own, in turn
    assert {timing.disconnect_outcome for timing in dep.timings} == {"timed_out"}
    assert probe.before("disconnect:HangingTop", "disconnect:HangingMiddle")
    assert probe.before("disconnect:HangingMiddle", "disconnect:HangingBottom")
    assert "DISCONNECT_DEADLINE_SECONDS" not in caplog.text


async def test_cancellation_before_the_deadline_still_propagates(caplog: pytest.LogCaptureFixture) -> None:
    # The deadline is the bound in force, and the cancellation of disconnect() is not taken for it running out
    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=10, disconnect_deadline=5))
    dep.resolve(HangsOnDisconnect)
    await dep.connect()

    with caplog.at_level(logging.ERROR, logger="nuke_di.core"):
        task = asyncio.create_task(dep.disconnect())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    timings = {timing.name: timing for timing in dep.timings}
    assert timings["HangsOnDisconnect"].disconnect_outcome == "cancelled"
    # Postgres, waiting for its consumer, is neither disconnected nor reported as skipped
    assert timings["Postgres"].disconnect_outcome is None
    assert "DISCONNECT_DEADLINE_SECONDS" not in caplog.text
    assert dep.connected is False


class OutlivesItsTimeout(Tracked):
    async def disconnect(self) -> None:
        await probe.run("disconnect:OutlivesItsTimeout")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # Swallows the cancellation of its timeout and goes on, until disconnect() itself is cancelled
            await asyncio.sleep(10)


async def test_cancellation_after_the_deadline_still_reports_the_skipped_clients(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=10, disconnect_deadline=0.05))
    dep.resolve(OutlivesItsTimeout)
    dep.resolve(HangingMiddle)
    await dep.connect()

    with caplog.at_level(logging.ERROR, logger="nuke_di.core"):
        task = asyncio.create_task(dep.disconnect())
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    timings = {timing.name: timing for timing in dep.timings}
    assert timings["HangingMiddle"].disconnect_outcome == "timed_out"
    assert timings["HangingBottom"].disconnect_outcome is None
    assert timings["OutlivesItsTimeout"].disconnect_outcome == "cancelled"
    assert "Skipped disconnecting 1 client after DISCONNECT_DEADLINE_SECONDS (0.05s): HangingBottom" in caplog.text
    assert dep.connected is False


class SlowToStop(Tracked):
    """
    Resolved second, disconnects slowest: Postgres waiting for its first consumer only would stop under it.
    """

    def __init__(self, pg: Postgres) -> None:
        self.pg = pg

    async def disconnect(self) -> None:
        await probe.run("disconnect:SlowToStop")
        await asyncio.sleep(0.05)
        probe.events.append("disconnect:SlowToStop:done")


class QuickToStop(Tracked):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


async def test_dependency_disconnects_after_every_consumer() -> None:
    dep = Dependencies()
    dep.resolve(QuickToStop)
    dep.resolve(SlowToStop)

    async with dep:
        ...

    assert probe.events.index("disconnect:SlowToStop:done") < probe.events.index("disconnect:Postgres:start")
    assert probe.before("disconnect:QuickToStop", "disconnect:Postgres")


class SlowToStart(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.05)
        probe.events.append("connect:SlowToStart:done")


class NeedsBoth(Tracked):
    # The slow dependency second: a client waiting for its first dependency only would start under it
    def __init__(self, pg: Postgres, slow: SlowToStart) -> None:
        self.slow = slow
        self.pg = pg


async def test_client_connects_after_every_dependency() -> None:
    dep = Dependencies()
    dep.resolve(NeedsBoth)

    async with dep:
        assert probe.events.index("connect:SlowToStart:done") < probe.events.index("connect:NeedsBoth:start")
        assert probe.before("connect:Postgres", "connect:NeedsBoth")


class Waits(Tracked):
    def __init__(self, slow: Slow) -> None:
        self.slow = slow


async def test_cancelled_connect_cancels_the_clients_waiting_for_dependencies() -> None:
    dep = Dependencies()
    dep.resolve(Waits)
    dep.resolve(Postgres)

    task = asyncio.create_task(dep.connect())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    timings = {timing.name: timing for timing in dep.timings}
    assert timings["Slow"].connect_outcome == "cancelled"
    assert timings["Waits"].connect_outcome is None
    assert timings["Postgres"].disconnect_outcome == "ok"
    assert dep.connected is False
    assert "connect:Waits:start" not in probe.events
