import asyncio
import copy
import inspect
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, MutableMapping
from contextlib import asynccontextmanager
from typing import assert_type

import pytest

from nuke_di import Client, ConnectTimeoutError, Dependencies, DependenciesSettings


class Database(Client):
    def __init__(self) -> None:
        self.connected = False

    async def connect(self) -> None:
        self.connected = True


class Cache(Client):
    """A class outside the resolved dependency graph."""


class PausesOnDisconnect(Client):
    started: asyncio.Event
    release: asyncio.Event

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def disconnect(self) -> None:
        self.started.set()
        await self.release.wait()


def test_container_constructor_identity_and_read_only_clients() -> None:
    deps = Dependencies()
    peer = Dependencies()

    assert list(inspect.signature(Dependencies).parameters) == ["settings"]
    assert deps is not peer and deps != peer
    assert len({deps, peer}) == 2
    assert repr(deps) == "Dependencies(connected=False, clients=[], replacements=[])"


def test_clients_is_a_read_only_snapshot() -> None:
    deps = Dependencies()
    database = deps.resolve(Database)
    assert deps.clients == {Database: database}
    assert not isinstance(deps.clients, MutableMapping)
    assert repr(deps) == "Dependencies(connected=False, clients=[Database], replacements=[])"


async def test_resolve_reads_existing_clients_inside_the_lifecycle() -> None:
    deps = Dependencies()
    database = deps.resolve(Database)
    cached = deps.mock(Cache, Cache())

    async with deps:
        assert assert_type(deps.resolve(Database), Database) is database
        assert deps.resolve(Database).connected
        assert deps.resolve(Cache) is cached
        with pytest.raises(RuntimeError, match="already connected"):
            deps.resolve(PausesOnDisconnect)
        assert repr(deps) == "Dependencies(connected=True, clients=[Database], replacements=[Cache])"


async def test_disconnect_holds_the_container_until_cleanup_finishes() -> None:
    deps = Dependencies()
    resource = deps.resolve(PausesOnDisconnect)
    await deps.connect()
    closing = asyncio.create_task(deps.disconnect())

    async with _finish_disconnect(resource, closing):
        await asyncio.wait_for(resource.started.wait(), timeout=1)
        with pytest.raises(RuntimeError, match="disconnecting"):
            deps.resolve(PausesOnDisconnect)
        with pytest.raises(RuntimeError, match="disconnecting"):
            deps.resolve(Cache)
        with pytest.raises(RuntimeError, match="disconnect"):
            await deps.connect()
        with pytest.raises(RuntimeError, match="disconnect"):
            await deps.disconnect()
        with pytest.raises(RuntimeError, match="disconnect"):
            deps.flush()

    assert not deps.connected
    assert deps.clients == {}


@asynccontextmanager
async def _finish_disconnect(resource: PausesOnDisconnect, closing: asyncio.Task[None]) -> AsyncIterator[None]:
    try:
        yield
    finally:
        resource.release.set()
        await closing


async def test_environment_is_read_once_when_the_container_first_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECT_TIMEOUT_SECONDS", "10")
    deps = Dependencies()
    monkeypatch.setenv("CONNECT_TIMEOUT_SECONDS", "0")
    deps.resolve(Database)
    with pytest.raises(ConnectTimeoutError):
        await deps.connect()

    monkeypatch.setenv("CONNECT_TIMEOUT_SECONDS", "10")
    deps.resolve(Database)
    with pytest.raises(ConnectTimeoutError):
        await deps.connect()


async def test_invalid_environment_can_be_fixed_before_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = Dependencies()
    database = deps.resolve(Database)
    monkeypatch.setenv("CONNECT_TIMEOUT_SECONDS", "nan")

    with pytest.raises(ValueError, match="connect_timeout"):
        await deps.connect()

    assert deps.resolve(Database) is database
    monkeypatch.setenv("CONNECT_TIMEOUT_SECONDS", "10")
    async with deps:
        assert database.connected


async def test_explicit_settings_take_precedence_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = DependenciesSettings(connect_timeout=10, disconnect_timeout=1, disconnect_deadline=1)
    deps = Dependencies(settings=settings)
    database = deps.resolve(Database)
    monkeypatch.setenv("CONNECT_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("DISCONNECT_TIMEOUT_SECONDS", "invalid")
    async with deps:
        assert database.connected


@pytest.mark.parametrize("invalid", [-1.0, float("nan")])
def test_connect_timeout_rejects_invalid_values(invalid: float) -> None:
    with pytest.raises(ValueError, match="connect_timeout"):
        DependenciesSettings(connect_timeout=invalid)


def test_global_di_does_not_read_configuration_at_import() -> None:
    script = """
import asyncio
import os

os.environ["CONNECT_TIMEOUT_SECONDS"] = "invalid"
from nuke_di import Client, ConnectTimeoutError, DI

os.environ["CONNECT_TIMEOUT_SECONDS"] = "0"
DI.resolve(Client)
try:
    asyncio.run(DI.connect())
except ConnectTimeoutError:
    assert not DI.connected
else:
    raise AssertionError("DI did not use the environment at its first connect")
"""
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("copier", [copy.copy, copy.deepcopy])
def test_a_container_cannot_be_copied(copier: Callable[[Dependencies], Dependencies]) -> None:
    with pytest.raises(TypeError):
        copier(Dependencies())
