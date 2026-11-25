import asyncio

import pytest

from nuke_di import (
    Client,
    ConnectError,
    ConnectTimeoutError,
    Dependencies,
    DependenciesSettings,
    InitializeDependencyError,
)


class SlowClient(Client):
    async def connect(self) -> None:
        await asyncio.sleep(1)


class BrokenConnectClient(Client):
    async def connect(self) -> None:
        raise RuntimeError("boom")


class BrokenInitClient(Client):
    def __init__(self) -> None:
        raise RuntimeError("boom")


class BrokenDisconnectClient(Client):
    async def disconnect(self) -> None:
        raise RuntimeError("boom")


class TrackedClient(Client):
    disconnected = False

    async def disconnect(self) -> None:
        TrackedClient.disconnected = True


async def test_connect_timeout() -> None:
    dep = Dependencies(settings=DependenciesSettings(connect_timeout=0.01))
    dep.resolve(SlowClient)

    with pytest.raises(ConnectTimeoutError) as exc_info:
        await dep.connect()

    assert isinstance(exc_info.value.__cause__, TimeoutError)


async def test_connect_error() -> None:
    dep = Dependencies()
    dep.resolve(BrokenConnectClient)

    with pytest.raises(ConnectError) as exc_info:
        await dep.connect()

    assert not isinstance(exc_info.value, ConnectTimeoutError)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_initialize_error() -> None:
    dep = Dependencies()

    with pytest.raises(InitializeDependencyError):
        dep.resolve(BrokenInitClient)


async def test_disconnect_continues_after_failure() -> None:
    dep = Dependencies()
    # disconnect goes in reverse order, so the broken client is stopped first
    dep.resolve(TrackedClient)
    dep.resolve(BrokenDisconnectClient)

    async with dep:
        ...

    assert TrackedClient.disconnected
    assert not dep.clients


async def test_connected_state_guards() -> None:
    dep = Dependencies()

    with pytest.raises(RuntimeError):
        await dep.disconnect()

    async with dep:
        with pytest.raises(RuntimeError):
            await dep.connect()
        with pytest.raises(RuntimeError):
            dep.resolve(TrackedClient)
        with pytest.raises(RuntimeError):
            dep.inject(test_initialize_error)
        with pytest.raises(RuntimeError):
            dep.mock(TrackedClient)


class HangingDisconnectClient(Client):
    async def disconnect(self) -> None:
        await asyncio.sleep(10)


async def test_disconnect_timeout(caplog: pytest.LogCaptureFixture) -> None:
    TrackedClient.disconnected = False
    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=0.01))
    dep.resolve(TrackedClient)
    dep.resolve(HangingDisconnectClient)

    async with dep:
        ...

    assert TrackedClient.disconnected
    assert "HangingDisconnectClient did not disconnect within 0.01s (DISCONNECT_TIMEOUT_SECONDS)" in caplog.text


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("connect_timeout", "5", 5),
        ("disconnect_timeout", "2.5", 2.5),
        ("disconnect_deadline", "2.5", 2.5),
        ("disconnect_deadline", "inf", float("inf")),
    ],
)
def test_settings_from_env(monkeypatch: pytest.MonkeyPatch, field: str, raw: str, expected: float) -> None:
    monkeypatch.setenv(f"{field.upper()}_SECONDS", raw)

    assert getattr(DependenciesSettings(), field) == expected


@pytest.mark.parametrize(
    ("field", "expected"), [("connect_timeout", 30), ("disconnect_timeout", 10), ("disconnect_deadline", 20)]
)
def test_settings_default(monkeypatch: pytest.MonkeyPatch, field: str, expected: float) -> None:
    monkeypatch.delenv(f"{field.upper()}_SECONDS", raising=False)

    assert getattr(DependenciesSettings(), field) == expected


@pytest.mark.parametrize("field", ["disconnect_timeout", "disconnect_deadline"])
@pytest.mark.parametrize("invalid", [-1, float("nan")])
def test_settings_reject_invalid_value(field: str, invalid: float) -> None:
    with pytest.raises(ValueError, match=field):
        DependenciesSettings(**{field: invalid})


def test_zero_disconnect_deadline() -> None:
    assert DependenciesSettings(disconnect_deadline=0).disconnect_deadline == 0


def test_dependencies_leave_settings_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECT_TIMEOUT_SECONDS", "5")

    assert Dependencies().settings is None
