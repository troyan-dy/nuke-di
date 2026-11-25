import asyncio
import logging
import re

import pytest

from nuke_di import Client, ConnectError, ConnectTimeoutError, Dependencies, DependenciesSettings
from tests.sequences import single

SLOW_CONNECT = 0.05
SLOW_DISCONNECT = 0.02


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.05)

    async def disconnect(self) -> None:
        await asyncio.sleep(0.02)


class Redis(Client):
    """
    A client with nothing to do: connect() and disconnect() of the base class.
    """


class Payments(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Broken(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.01)
        raise RuntimeError("boom")


class Untouched(Client):
    """
    A client whose connect() and disconnect() are expected never to run.
    """

    touched = False

    async def connect(self) -> None:
        Untouched.touched = True

    async def disconnect(self) -> None:
        Untouched.touched = True


async def test_zero_timeout_expires_before_the_client_runs(caplog: pytest.LogCaptureFixture) -> None:
    # As `asyncio.wait_for(..., timeout=0)` did: the client's coroutine is not even started
    Untouched.touched = False
    dep = Dependencies(settings=DependenciesSettings(connect_timeout=0))
    dep.resolve(Untouched)
    with pytest.raises(ConnectTimeoutError) as exc_info:
        await dep.connect()
    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert Untouched.touched is False
    assert "Untouched did not connect within 0s (CONNECT_TIMEOUT_SECONDS)" in caplog.text

    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=0))
    dep.resolve(Untouched)
    await dep.connect()
    Untouched.touched = False
    await dep.disconnect()
    assert Untouched.touched is False
    assert "Untouched did not disconnect within 0s (DISCONNECT_TIMEOUT_SECONDS)" in caplog.text


async def test_startup_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Payments)
    dep.resolve(Redis)

    async with dep:
        ...

    summaries = (record for record in caplog.records if record.getMessage().startswith("Connected 3 clients"))
    summary = single(summaries)
    assert summary.levelno == logging.INFO
    assert summary.getMessage().startswith("Connected 3 clients in 0.")
    assert summary.duration >= SLOW_CONNECT  # type: ignore[attr-defined]
    assert summary.name == "nuke_di"


async def test_startup_summary_singular(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Redis)

    async with dep:
        ...

    assert "Connected 1 client in 0." in caplog.text


async def test_empty_container_logs_no_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nuke_di")
    dep = Dependencies()

    async with dep:
        ...

    assert "Connected" not in caplog.text


async def test_failed_connect_logs_no_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Broken)

    with pytest.raises(ConnectError):
        await dep.connect()

    assert "Connected" not in caplog.text


async def test_slow_client_warning(caplog: pytest.LogCaptureFixture) -> None:
    dep = Dependencies(settings=DependenciesSettings(connect_timeout=0.09))
    dep.resolve(Postgres)
    dep.resolve(Redis)

    async with dep:
        ...

    warning = single(record for record in caplog.records if record.levelno == logging.WARNING)
    assert warning.getMessage().startswith("Client Postgres took 0.0")
    assert warning.getMessage().endswith("s to connect, more than half of CONNECT_TIMEOUT_SECONDS (0.09s)")
    assert warning.client == "Postgres"  # type: ignore[attr-defined]


async def test_records_carry_structured_fields(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Payments)

    async with dep:
        ...

    connected = single(
        record for record in caplog.records if record.getMessage().startswith("Connected client Payments")
    )
    assert connected.client == "Payments"  # type: ignore[attr-defined]
    assert not hasattr(connected, "layer")
    assert connected.duration >= 0  # type: ignore[attr-defined]

    for record in caplog.records:
        named = re.search(r"client (\w+)", record.getMessage())
        if named is not None:
            assert record.client == named[1], record.getMessage()  # type: ignore[attr-defined]


async def test_failure_records_carry_client(caplog: pytest.LogCaptureFixture) -> None:
    dep = Dependencies()
    dep.resolve(Broken)

    with pytest.raises(ConnectError):
        await dep.connect()

    error = single(record for record in caplog.records if record.levelno == logging.ERROR)
    assert error.client == "Broken"  # type: ignore[attr-defined]
