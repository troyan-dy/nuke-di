import ast
import asyncio
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, TypeGuard

import pytest

import nuke_di as package
from nuke_di import (
    BackgroundTasks,
    Client,
    ClientTiming,
    ConnectError,
    ConnectTimeoutError,
    Dependencies,
    DependenciesSettings,
    Run,
)
from nuke_di.run import RunRequest, RunSettings, run_entrypoint
from tests.sequences import single

# Postgres sleeps 0.05s in connect() and 0.02s in disconnect(): the lower bounds of their timings
SLOW_CONNECT = 0.04
SLOW_DISCONNECT = 0.01


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


class Slow(Client):
    async def connect(self) -> None:
        await asyncio.sleep(10)


class Consumer(Client):
    def __init__(self, broken: Broken, slow: Slow) -> None:
        self.broken = broken
        self.slow = slow


class Hanging(Client):
    async def connect(self) -> None:
        await asyncio.sleep(10)


class HangsOnDisconnect(Client):
    async def disconnect(self) -> None:
        await asyncio.sleep(10)


class FailsToDisconnect(Client):
    async def disconnect(self) -> None:
        raise RuntimeError("boom")


class BrokenInit(Client):
    def __init__(self) -> None:
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


class Sleepy(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.05)


class Sleepy2(Sleepy):
    """
    A second client that sleeps as long as Sleepy.
    """


# One Sleepy connect, without the 0.05s that the other one holds the only slot
WITHOUT_WAITING = 0.09


def by_name(timings: list[ClientTiming]) -> dict[str, ClientTiming]:
    return {timing.name: timing for timing in timings}


async def test_connect_records_every_client() -> None:
    dep = Dependencies()
    dep.resolve(Payments)
    dep.resolve(Redis)

    async with dep:
        timings = by_name(dep.timings)
        # Resolution order: a client after its dependencies
        assert [timing.name for timing in dep.timings] == ["Postgres", "Payments", "Redis"]
        postgres = timings["Postgres"]
        assert postgres.connect is not None
        assert postgres.connect >= SLOW_CONNECT
        assert (postgres.connect_outcome, postgres.disconnect, postgres.disconnect_outcome) == ("ok", None, None)


async def test_timings_outlive_disconnect() -> None:
    dep = Dependencies()
    dep.resolve(Postgres)

    async with dep:
        ...

    timing = single(dep.timings)
    assert timing.disconnect_outcome == "ok"
    assert timing.disconnect is not None
    assert timing.disconnect >= SLOW_DISCONNECT


async def test_next_connect_starts_new_timings() -> None:
    dep = Dependencies()
    dep.resolve(Postgres)
    async with dep:
        ...

    dep.resolve(Redis)
    async with dep:
        assert [timing.name for timing in dep.timings] == ["Redis"]


async def test_failed_connect_records_outcomes() -> None:
    dep = Dependencies()
    dep.resolve(Consumer)
    dep.resolve(Redis)

    with pytest.raises(ConnectError):
        await dep.connect()

    timings = by_name(dep.timings)
    assert timings["Broken"].connect_outcome == "failed"
    assert timings["Slow"].connect_outcome == "cancelled"
    # Never started: its dependency failed first
    assert (timings["Consumer"].connect, timings["Consumer"].connect_outcome) == (None, None)
    # Connected, then rolled back
    assert (timings["Redis"].connect_outcome, timings["Redis"].disconnect_outcome) == ("ok", "ok")
    assert timings["Broken"].disconnect_outcome is None


async def test_timeouts_and_disconnect_failure() -> None:
    dep = Dependencies(settings=DependenciesSettings(connect_timeout=0.01, disconnect_timeout=0.01))
    dep.resolve(Hanging)
    with pytest.raises(ConnectError):
        await dep.connect()
    assert dep.timings[0].connect_outcome == "timed_out"

    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=0.01))
    dep.resolve(HangsOnDisconnect)
    dep.resolve(FailsToDisconnect)
    async with dep:
        ...
    timings = by_name(dep.timings)
    assert timings["HangsOnDisconnect"].disconnect_outcome == "timed_out"
    assert timings["FailsToDisconnect"].disconnect_outcome == "failed"


async def test_zero_timeout_expires_before_the_client_runs(caplog: pytest.LogCaptureFixture) -> None:
    # As `asyncio.wait_for(..., timeout=0)` did: the client's coroutine is not even started
    Untouched.touched = False
    dep = Dependencies(settings=DependenciesSettings(connect_timeout=0))
    dep.resolve(Untouched)
    with pytest.raises(ConnectTimeoutError) as exc_info:
        await dep.connect()
    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert (dep.timings[0].connect_outcome, Untouched.touched) == ("timed_out", False)
    assert "Untouched did not connect within 0s (CONNECT_TIMEOUT_SECONDS)" in caplog.text

    dep = Dependencies(settings=DependenciesSettings(disconnect_timeout=0))
    dep.resolve(Untouched)
    await dep.connect()
    Untouched.touched = False
    await dep.disconnect()
    assert (dep.timings[0].disconnect_outcome, Untouched.touched) == ("timed_out", False)
    assert "Untouched did not disconnect within 0s (DISCONNECT_TIMEOUT_SECONDS)" in caplog.text


async def test_cancelled_connect_is_recorded() -> None:
    dep = Dependencies()
    dep.resolve(Hanging)

    task = asyncio.create_task(dep.connect())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cancelled from outside, well within its timeout: not a timeout
    assert dep.timings[0].connect_outcome == "cancelled"


async def test_cancelled_disconnect_is_recorded() -> None:
    dep = Dependencies()
    dep.resolve(HangsOnDisconnect)
    await dep.connect()

    task = asyncio.create_task(dep.disconnect())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert dep.timings[0].disconnect_outcome == "cancelled"


async def test_duration_excludes_waiting_for_concurrency() -> None:
    dep = Dependencies(settings=DependenciesSettings(connect_concurrency=1))
    dep.resolve(Sleepy)
    dep.resolve(Sleepy2)

    async with dep:
        for timing in dep.timings:
            assert timing.connect is not None
            assert timing.connect < WITHOUT_WAITING


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
    assert "(slowest: Postgres 0.0" in summary.getMessage()
    assert summary.duration >= SLOW_CONNECT  # type: ignore[attr-defined]
    # The log lines of the guide print both (docs/guide/litestar.md)
    assert (summary.name, summary.module) == ("nuke_di.core", "core")


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


async def start(func: Any, container: Dependencies, hooks: list[Any] | None = None) -> Run:
    request = RunRequest(
        func=func,
        kind="job",
        name="tests.entry",
        hooks=hooks or [],
        container=container,
        settings=RunSettings(shutdown_grace=1),
    )
    return await run_entrypoint(request)


class Collect:
    def __init__(self) -> None:
        self.clients: list[ClientTiming] = []

    async def on_start(self, run: Run) -> None:
        assert not run.clients

    async def on_finish(self, run: Run) -> None:
        self.clients = run.clients


async def test_run_carries_client_timings() -> None:
    async def entry(pg: Postgres) -> None: ...

    hook = Collect()
    await start(entry, Dependencies(), hooks=[hook])

    timings = by_name(hook.clients)
    assert timings["Postgres"].connect_outcome == "ok"
    assert timings["Postgres"].disconnect_outcome == "ok"


async def test_run_failed_before_connect_has_no_timings() -> None:
    container = Dependencies()
    container.resolve(Postgres)
    async with container:
        ...

    async def entry(pg: Postgres, broken: BrokenInit) -> None: ...

    run = await start(entry, container)

    assert run.error is not None
    assert not run.clients


async def test_records_of_a_run_carry_its_name(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="nuke_di")

    async def entry(pg: Postgres, tasks: BackgroundTasks) -> None:
        async def broken() -> None:
            raise RuntimeError("boom")

        tasks.spawn(broken(), name="broken")
        await asyncio.sleep(0.1)

    await start(entry, Dependencies())

    messages = {record.getMessage(): record for record in caplog.records}
    assert "Background task broken failed" in messages
    assert "Connected client Postgres in" in caplog.text
    for record in caplog.records:
        assert record.run == "tests.entry", record.getMessage()  # type: ignore[attr-defined]
    finished = single(each for each in caplog.records if "finished with exit code" in each.getMessage())
    assert finished.duration >= 0  # type: ignore[attr-defined]


async def test_records_outside_a_run_have_no_run(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Postgres)

    async with dep:
        ...

    assert caplog.records
    assert not any(hasattr(record, "run") for record in caplog.records)


def _logs_without_fields(node: ast.AST) -> TypeGuard[ast.Call]:
    """
    Whether `node` is a `logger.<method>(...)` call that writes a record without `extra`.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    owner = func.value
    if not isinstance(owner, ast.Name) or owner.id != "logger":
        return False
    # `isEnabledFor` asks the logger, it does not write a record
    if func.attr == "isEnabledFor":
        return False
    return not any(keyword.arg == "extra" for keyword in node.keywords)


def test_every_log_call_passes_structured_fields() -> None:
    package_dir = Path(package.__file__).parent
    calls = [
        (str(path.relative_to(package_dir)), node.lineno)
        # The subpackages too: the container logs from nuke_di._container
        for path in package_dir.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if _logs_without_fields(node)
    ]

    assert not calls


def test_import_skips_unittest_and_argparse() -> None:
    code = "import sys, nuke_di; print(sorted({'unittest', 'argparse'} & set(sys.modules)))"
    # A fresh interpreter: pytest itself has imported both
    command = [sys.executable, "-c", code]
    run = subprocess.run(command, capture_output=True, text=True, check=True)  # noqa: S603 - fixed code

    assert run.stdout.strip() == "[]"


async def test_client_cancelled_while_waiting_for_a_slot_never_started() -> None:
    dep = Dependencies(settings=DependenciesSettings(connect_concurrency=1))
    dep.resolve(Slow)
    dep.resolve(Sleepy)

    task = asyncio.create_task(dep.connect())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    timings = by_name(dep.timings)
    assert timings["Slow"].connect_outcome == "cancelled"
    assert timings["Sleepy"].connect is None
    assert timings["Sleepy"].connect_outcome is None


def test_only_the_name_is_positional() -> None:
    # A second positional argument was the layer up to 1.12: it must not land in `connect` silently
    with pytest.raises(TypeError):
        ClientTiming("Orders", 1)  # type: ignore[call-arg]
