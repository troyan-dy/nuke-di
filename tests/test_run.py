import asyncio
import logging
import os
import signal
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from nuke_di import BackgroundTasks, Client, ConnectError, Dependencies, InitializeDependencyError, Run, Shutdown
from nuke_di.run import RunSettings, run_entrypoint


class Events:
    def __init__(self) -> None:
        self.log: list[str] = []


events = Events()


@pytest.fixture(autouse=True)
def reset_events() -> None:
    events.log = []


def send(sig: signal.Signals) -> None:
    # Never deliver a signal nobody handles: SIGTERM would kill the test runner
    assert signal.getsignal(sig) not in {signal.SIG_DFL, signal.SIG_IGN}
    os.kill(os.getpid(), sig)


class Db(Client):
    async def connect(self) -> None:
        events.log.append("connect:Db")

    async def disconnect(self) -> None:
        events.log.append("disconnect:Db")


class Broken(Client):
    async def connect(self) -> None:
        raise RuntimeError("boom")


class BrokenInit(Client):
    def __init__(self) -> None:
        raise RuntimeError("boom")


class Hanging(Client):
    def __init__(self, db: Db) -> None:
        self.db = db

    async def connect(self) -> None:
        send(signal.SIGTERM)
        await asyncio.sleep(10)


class SignalsOnDisconnect(Client):
    async def disconnect(self) -> None:
        send(signal.SIGTERM)
        await asyncio.sleep(0.01)


async def start(
    func: Callable[..., Any],
    *,
    kind: str = "job",
    hooks: Sequence[Any] = (),
    grace: float = 10,
    container: Dependencies | None = None,
) -> Run:
    return await run_entrypoint(
        func,
        kind=kind,  # type: ignore[arg-type]
        name="tests.entry",
        hooks=hooks,
        container=container or Dependencies(),
        settings=RunSettings(shutdown_grace=grace),
    )


async def test_job_succeeds() -> None:
    async def entry(db: Db) -> None:
        events.log.append(f"run:{type(db).__name__}")

    run = await start(entry)

    assert run.exit_code == 0
    assert run.error is None
    assert run.signal is None
    assert events.log == ["connect:Db", "run:Db", "disconnect:Db"]


async def test_run_records_metadata() -> None:
    async def entry() -> None:
        pass

    run = await start(entry, kind="worker")

    assert run.name == "tests.entry"
    assert run.kind == "worker"
    assert run.finished_at is not None
    assert run.started_at <= run.finished_at
    assert run.started_at.tzinfo is not None


async def test_entrypoint_error_fails_run(caplog: pytest.LogCaptureFixture) -> None:
    error = RuntimeError("boom")

    async def entry(db: Db) -> None:
        raise error

    run = await start(entry)

    assert run.exit_code == 1
    assert run.error is error
    assert events.log == ["connect:Db", "disconnect:Db"]
    assert "Run tests.entry failed" in caplog.text


async def test_connect_error_fails_run() -> None:
    async def entry(broken: Broken) -> None:
        events.log.append("run")

    run = await start(entry)

    assert run.exit_code == 1
    assert isinstance(run.error, ConnectError)
    assert events.log == []


async def test_resolution_error_fails_run() -> None:
    async def entry(broken: BrokenInit) -> None:
        pass

    run = await start(entry)

    assert run.exit_code == 1
    assert isinstance(run.error, InitializeDependencyError)


async def test_invalid_signature_fails_run() -> None:
    async def entry(db) -> None:  # type: ignore[no-untyped-def]
        pass

    run = await start(entry)

    assert run.exit_code == 1
    assert isinstance(run.error, TypeError)


async def test_worker_ending_on_its_own_ends_run() -> None:
    async def entry() -> None:
        pass

    run = await start(entry, kind="worker")

    assert run.exit_code == 0


async def test_cooperative_shutdown(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nuke_di")

    async def entry(db: Db, shutdown: Shutdown) -> None:
        send(signal.SIGTERM)
        await shutdown.wait()
        events.log.append("stopped")

    run = await start(entry, kind="worker")

    assert run.exit_code == 128 + signal.SIGTERM
    assert run.signal == signal.SIGTERM
    assert run.error is None
    assert events.log == ["connect:Db", "stopped", "disconnect:Db"]
    assert "Shutdown requested by SIGTERM" in caplog.text


async def test_entrypoint_is_cancelled_after_grace(caplog: pytest.LogCaptureFixture) -> None:
    async def entry(db: Db) -> None:
        send(signal.SIGTERM)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            events.log.append("cancelled")
            raise

    run = await start(entry, kind="worker", grace=0.01)

    assert run.exit_code == 128 + signal.SIGTERM
    assert run.error is None
    assert events.log == ["connect:Db", "cancelled", "disconnect:Db"]
    assert "did not stop within 0.01s" in caplog.text


async def test_second_signal_cancels_immediately() -> None:
    async def entry() -> None:
        send(signal.SIGINT)
        await asyncio.sleep(0.01)
        send(signal.SIGINT)
        await asyncio.sleep(10)

    run = await asyncio.wait_for(start(entry, kind="worker", grace=10), timeout=2)

    assert run.exit_code == 128 + signal.SIGINT


async def test_signal_during_connect_disconnects_connected_clients() -> None:
    async def entry(hanging: Hanging) -> None:
        events.log.append("run")

    run = await asyncio.wait_for(start(entry), timeout=2)

    assert run.exit_code == 128 + signal.SIGTERM
    assert run.error is None
    assert events.log == ["connect:Db", "disconnect:Db"]


async def test_signal_after_entrypoint_ended_is_ignored() -> None:
    async def entry(client: SignalsOnDisconnect) -> None:
        pass

    run = await start(entry)

    assert run.exit_code == 0
    assert run.signal is None


async def test_failing_background_task_fails_run() -> None:
    error = RuntimeError("boom")

    async def broken() -> None:
        raise error

    async def entry(tasks: BackgroundTasks, shutdown: Shutdown) -> None:
        tasks.spawn(broken())
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            events.log.append(f"cancelled, shutdown={shutdown.is_set()}")
            raise

    run = await asyncio.wait_for(start(entry, kind="worker"), timeout=2)

    assert run.exit_code == 1
    assert run.error is error
    assert events.log == ["cancelled, shutdown=False"]


async def test_first_error_wins() -> None:
    first = RuntimeError("background")

    async def broken() -> None:
        raise first

    async def entry(tasks: BackgroundTasks) -> None:
        tasks.spawn(broken())
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise RuntimeError("entrypoint") from None

    run = await asyncio.wait_for(start(entry, kind="worker"), timeout=2)

    assert run.error is first


async def test_background_tasks_stop_before_clients_disconnect() -> None:
    async def loop() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            events.log.append("task:stopped")

    async def entry(db: Db, tasks: BackgroundTasks) -> None:
        tasks.spawn(loop())
        await asyncio.sleep(0)

    run = await start(entry)

    assert run.exit_code == 0
    assert events.log == ["connect:Db", "task:stopped", "disconnect:Db"]


async def test_error_wins_over_signal() -> None:
    async def entry() -> None:
        send(signal.SIGTERM)
        await asyncio.sleep(0.01)
        raise RuntimeError("boom")

    run = await start(entry)

    assert run.exit_code == 1
    assert run.signal == signal.SIGTERM


async def test_signal_handlers_are_removed() -> None:
    async def entry() -> None:
        pass

    before = signal.getsignal(signal.SIGTERM)
    await start(entry)

    assert signal.getsignal(signal.SIGTERM) == before


async def test_falls_back_to_sigint_only(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()

    def unsupported(*args: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(loop, "add_signal_handler", unsupported)
    before_term = signal.getsignal(signal.SIGTERM)
    before_int = signal.getsignal(signal.SIGINT)

    async def entry(shutdown: Shutdown) -> None:
        assert signal.getsignal(signal.SIGTERM) == before_term
        send(signal.SIGINT)
        await shutdown.wait()

    run = await asyncio.wait_for(start(entry), timeout=2)

    assert run.exit_code == 128 + signal.SIGINT
    assert signal.getsignal(signal.SIGINT) == before_int


class Hook:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.finished: Run | None = None

    async def on_start(self, run: Run) -> None:
        events.log.append(f"start:{self.name}:{run.exit_code}")
        if self.fail:
            raise RuntimeError("hook")

    async def on_finish(self, run: Run) -> None:
        events.log.append(f"finish:{self.name}:{run.exit_code}")
        self.finished = run
        if self.fail:
            raise RuntimeError("hook")


async def test_hooks_wrap_the_run() -> None:
    async def entry(db: Db) -> None:
        events.log.append("run")

    await start(entry, hooks=[Hook("a"), Hook("b")])

    assert events.log == [
        "start:a:None",
        "start:b:None",
        "connect:Db",
        "run",
        "disconnect:Db",
        "finish:b:0",
        "finish:a:0",
    ]


async def test_hooks_see_connect_failure() -> None:
    async def entry(broken: Broken) -> None:
        pass

    hook = Hook("a")
    await start(entry, hooks=[hook])

    assert hook.finished is not None
    assert hook.finished.exit_code == 1
    assert isinstance(hook.finished.error, ConnectError)


async def test_failing_hook_does_not_change_exit_code(caplog: pytest.LogCaptureFixture) -> None:
    async def entry() -> None:
        pass

    run = await start(entry, hooks=[Hook("a", fail=True), Hook("b")])

    assert run.exit_code == 0
    assert events.log == ["start:a:None", "start:b:None", "finish:b:0", "finish:a:0"]
    assert "Hook" in caplog.text


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHUTDOWN_GRACE_SECONDS", "2.5")

    assert RunSettings().shutdown_grace == 2.5


def test_settings_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHUTDOWN_GRACE_SECONDS", raising=False)

    assert RunSettings().shutdown_grace == 10


def test_negative_grace() -> None:
    with pytest.raises(ValueError, match="shutdown_grace"):
        RunSettings(shutdown_grace=-1)
