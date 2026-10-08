import ast
import asyncio
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import nuke_di
from nuke_di import BackgroundTasks, Client, ClientTiming, ConnectError, Dependencies, DependenciesSettings, Run
from nuke_di.run import RunSettings, run_entrypoint


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.05)

    async def disconnect(self) -> None:
        await asyncio.sleep(0.02)


class Redis(Client):
    pass


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
        self.broken, self.slow = broken, slow


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


class Sleepy(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.05)


class Sleepy2(Sleepy):
    pass


def by_name(timings: list[ClientTiming]) -> dict[str, ClientTiming]:
    return {timing.name: timing for timing in timings}


async def test_connect_records_every_client() -> None:
    dep = Dependencies()
    dep.resolve(Payments)
    dep.resolve(Redis)

    async with dep:
        timings = by_name(dep.timings)
        assert [timing.name for timing in dep.timings] == ["Postgres", "Redis", "Payments"]
        assert timings["Postgres"].layer == 0
        assert timings["Payments"].layer == 1
        assert timings["Postgres"].connect_outcome == "ok"
        assert timings["Postgres"].connect is not None
        assert timings["Postgres"].connect >= 0.04
        assert timings["Postgres"].disconnect is None
        assert timings["Postgres"].disconnect_outcome is None


async def test_timings_outlive_disconnect() -> None:
    dep = Dependencies()
    dep.resolve(Postgres)

    async with dep:
        pass

    [timing] = dep.timings
    assert timing.disconnect_outcome == "ok"
    assert timing.disconnect is not None
    assert timing.disconnect >= 0.01


async def test_next_connect_starts_new_timings() -> None:
    dep = Dependencies()
    dep.resolve(Postgres)
    async with dep:
        pass

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
    # Never started: its layer was not reached
    assert timings["Consumer"].connect is None
    assert timings["Consumer"].connect_outcome is None
    # Connected, then rolled back
    assert timings["Redis"].connect_outcome == "ok"
    assert timings["Redis"].disconnect_outcome == "ok"
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
        pass
    timings = by_name(dep.timings)
    assert timings["HangsOnDisconnect"].disconnect_outcome == "timed_out"
    assert timings["FailsToDisconnect"].disconnect_outcome == "failed"


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
            assert timing.connect < 0.09


async def test_startup_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Payments)
    dep.resolve(Redis)

    async with dep:
        pass

    [summary] = [record for record in caplog.records if record.getMessage().startswith("Connected 3 clients")]
    assert summary.levelno == logging.INFO
    assert summary.getMessage().startswith("Connected 3 clients in 2 layers in 0.")
    assert "(slowest: Postgres 0.0" in summary.getMessage()
    assert summary.duration >= 0.04  # type: ignore[attr-defined]


async def test_startup_summary_singular(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Redis)

    async with dep:
        pass

    assert "Connected 1 client in 1 layer in " in caplog.text


async def test_empty_container_logs_no_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nuke_di")
    dep = Dependencies()

    async with dep:
        pass

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
        pass

    [warning] = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warning.getMessage().startswith("Client Postgres took 0.0")
    assert warning.getMessage().endswith("s to connect, more than half of CONNECT_TIMEOUT_SECONDS (0.09s)")
    assert warning.client == "Postgres"  # type: ignore[attr-defined]


async def test_records_carry_structured_fields(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Payments)

    async with dep:
        pass

    connected = [record for record in caplog.records if record.getMessage().startswith("Connected client Payments")]
    [record] = connected
    assert record.client == "Payments"  # type: ignore[attr-defined]
    assert record.layer == 1  # type: ignore[attr-defined]
    assert record.duration >= 0  # type: ignore[attr-defined]

    for record in caplog.records:
        named = re.search(r"client (\w+)", record.getMessage())
        if named is not None:
            assert record.client == named[1], record.getMessage()  # type: ignore[attr-defined]
        layer = re.search(r"layer (\d+)", record.getMessage())
        if layer is not None:
            assert record.layer == int(layer[1]), record.getMessage()  # type: ignore[attr-defined]


async def test_failure_records_carry_client(caplog: pytest.LogCaptureFixture) -> None:
    dep = Dependencies()
    dep.resolve(Broken)

    with pytest.raises(ConnectError):
        await dep.connect()

    [error] = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert error.client == "Broken"  # type: ignore[attr-defined]
    assert error.layer == 0  # type: ignore[attr-defined]


async def start(func: Any, container: Dependencies, hooks: list[Any] | None = None) -> Run:
    return await run_entrypoint(
        func,
        kind="job",
        name="tests.entry",
        hooks=hooks or [],
        container=container,
        settings=RunSettings(shutdown_grace=1),
    )


class Collect:
    def __init__(self) -> None:
        self.clients: list[ClientTiming] = []

    async def on_start(self, run: Run) -> None:
        assert run.clients == []

    async def on_finish(self, run: Run) -> None:
        self.clients = run.clients


async def test_run_carries_client_timings() -> None:
    async def entry(pg: Postgres) -> None:
        pass

    hook = Collect()
    await start(entry, Dependencies(), hooks=[hook])

    timings = by_name(hook.clients)
    assert timings["Postgres"].connect_outcome == "ok"
    assert timings["Postgres"].disconnect_outcome == "ok"


async def test_run_failed_before_connect_has_no_timings() -> None:
    container = Dependencies()
    container.resolve(Postgres)
    async with container:
        pass

    async def entry(pg: Postgres, broken: BrokenInit) -> None:
        pass

    run = await start(entry, container)

    assert run.error is not None
    assert run.clients == []


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
    [finished] = [record for record in caplog.records if "finished with exit code" in record.getMessage()]
    assert finished.duration >= 0  # type: ignore[attr-defined]


async def test_records_outside_a_run_have_no_run(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="nuke_di")
    dep = Dependencies()
    dep.resolve(Postgres)

    async with dep:
        pass

    assert caplog.records
    assert not any(hasattr(record, "run") for record in caplog.records)


def test_every_log_call_passes_structured_fields() -> None:
    package = Path(nuke_di.__file__).parent
    calls = [
        (path.name, node.lineno)
        for path in package.glob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
        and not any(keyword.arg == "extra" for keyword in node.keywords)
    ]

    assert calls == []


def test_import_skips_unittest_and_argparse() -> None:
    code = "import sys, nuke_di; print(sorted({'unittest', 'argparse'} & set(sys.modules)))"
    # A fresh interpreter: pytest itself has imported both
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)  # noqa: S603 - fixed code

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
