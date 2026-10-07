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

    with pytest.raises(ConnectTimeoutError):
        await dep.connect()


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
        pass

    assert TrackedClient.disconnected
    assert dep.connect_clients == []


async def test_connected_state_guards() -> None:
    dep = Dependencies()

    with pytest.raises(ConnectError):
        await dep.disconnect()

    async with dep:
        with pytest.raises(ConnectError):
            await dep.connect()
        with pytest.raises(ConnectError):
            dep.resolve(TrackedClient)
        with pytest.raises(ConnectError):
            dep.inject(test_initialize_error)
        with pytest.raises(ConnectError):
            dep.mock(TrackedClient)


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECT_TIMEOUT_SECONDS", "5")

    assert DependenciesSettings().connect_timeout == 5
    assert Dependencies().settings.connect_timeout == 5


def test_settings_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONNECT_TIMEOUT_SECONDS", raising=False)

    assert DependenciesSettings().connect_timeout == 30


class HangingDisconnectClient(Client):
    async def disconnect(self) -> None:
        await asyncio.sleep(10)


async def test_disconnect_timeout(caplog: pytest.LogCaptureFixture) -> None:
    TrackedClient.disconnected = False
    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=0.01))
    dep.resolve(TrackedClient)
    dep.resolve(HangingDisconnectClient)

    async with dep:
        pass

    assert TrackedClient.disconnected
    assert "Timeout occurred disconnecting client HangingDisconnectClient" in caplog.text


def test_disconnect_timeout_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCONNECT_TIMEOUT_SECONDS", "2.5")

    assert DependenciesSettings().disconnect_timeout == 2.5


def test_disconnect_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCONNECT_TIMEOUT_SECONDS", raising=False)

    assert DependenciesSettings().disconnect_timeout == 10


def test_negative_disconnect_timeout() -> None:
    with pytest.raises(ValueError, match="disconnect_timeout"):
        DependenciesSettings(disconnect_timeout=-1)
