import asyncio
import logging
from collections.abc import Iterator

import pytest

from nuke_di import Client, ConnectError, Dependencies, DependenciesSettings
from tests.test_resolve import chain


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
        finally:
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
    pass


class Redis(Tracked):
    pass


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
        pass

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
    pass


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
    assert probe.events == []


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

    [timing] = [timing for timing in dep.timings if timing.name == "Blocked"]
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
        pass

    assert probe.before("disconnect:FailsToDisconnect", "disconnect:Postgres")


async def test_long_chain_connects_and_disconnects() -> None:
    dep = Dependencies()
    dep.resolve(chain(2000)[-1])

    async with dep:
        assert [timing.connect_outcome for timing in dep.timings] == ["ok"] * 2000

    assert [timing.disconnect_outcome for timing in dep.timings] == ["ok"] * 2000


async def test_progress_in_the_client_records(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Checkout)

    async with dep:
        pass

    messages = [record.getMessage() for record in caplog.records if " client " in record.getMessage()]
    assert messages == [
        "Connecting client Postgres (0/3 connected)",
        "Connected client Postgres in 0.0" + messages[1].split(" in 0.0")[1],
        "Connecting client Payments (1/3 connected)",
        "Connected client Payments in 0.0" + messages[3].split(" in 0.0")[1],
        "Connecting client Checkout (2/3 connected)",
        "Connected client Checkout in 0.0" + messages[5].split(" in 0.0")[1],
        "Disconnecting client Checkout (0/3 disconnected)",
        "Disconnected client Checkout in 0.0" + messages[7].split(" in 0.0")[1],
        "Disconnecting client Payments (1/3 disconnected)",
        "Disconnected client Payments in 0.0" + messages[9].split(" in 0.0")[1],
        "Disconnecting client Postgres (2/3 disconnected)",
        "Disconnected client Postgres in 0.0" + messages[11].split(" in 0.0")[1],
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
        with pytest.raises(ConnectError, match=r"CancelsItself\.connect\(\) raised CancelledError"):
            await dep.connect()

    assert probe.events == []
    [timing] = [timing for timing in dep.timings if timing.name == "CancelsItself"]
    assert timing.connect_outcome == "cancelled"
