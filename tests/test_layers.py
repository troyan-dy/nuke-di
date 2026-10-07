import asyncio
from collections.abc import Iterator

import pytest

from nuke_di import Client, ConnectError, Dependencies, DependenciesSettings


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


async def test_layer_connects_concurrently() -> None:
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


async def test_layer_disconnects_concurrently() -> None:
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


async def test_mocked_dependency_does_not_lift_layer() -> None:
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


async def test_failure_cancels_rest_of_layer() -> None:
    dep = Dependencies()
    dep.resolve(Broken)
    dep.resolve(AfterSlow)

    with pytest.raises(ConnectError, match="Broken") as exc_info:
        await dep.connect()

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert Slow.cancelled
    # the next layer never starts
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
