"""
The examples in examples/ keep working: the tests of every example pass, and its programs run with the
exit code and the key lines of output their README shows. Every example runs in a process of its own, from
examples/, as its README tells a reader to run it. The examples on NATS run when a server listens on
localhost:4222, as in CI; the web servers are left to the tests of their examples, since uvicorn is not
installed.
"""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NAMES = sorted(path.parent.name for path in EXAMPLES.glob("*/__init__.py"))
TIMEOUT = 60
# What the examples and the library read from the environment: a test sets them, never whoever runs the tests
CONFIGURATION = {
    "DATABASE_URL",
    "DEBUG",
    "HTTP_TIMEOUT_SECONDS",
    "NATS_URL",
    "OUTBOX_POLL_SECONDS",
    "POOL_SIZE",
    "PYPI_URL",
    "QUEUE_DB",
    "SERVICE_DB",
    "SQLITE_PATH",
    "CONNECT_CONCURRENCY",
    "CONNECT_TIMEOUT_SECONDS",
    "DISCONNECT_TIMEOUT_SECONDS",
    "SHUTDOWN_GRACE_SECONDS",
}


def environment(**extra: str) -> dict[str, str]:
    # Coverage of the outer run must not follow into the example processes either
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("COV_CORE_") and key not in CONFIGURATION
    }
    env.update(PYTHONUNBUFFERED="1", **extra)
    return env


def nats_is_running() -> bool:
    try:
        socket.create_connection(("localhost", 4222), timeout=0.5).close()
    except OSError:
        return False
    return True


needs_nats = pytest.mark.skipif(not nats_is_running(), reason="no NATS server on localhost:4222")


def test_every_example_has_a_readme() -> None:
    assert NAMES
    for name in NAMES:
        assert (EXAMPLES / name / "README.md").is_file(), name


def test_every_example_is_listed() -> None:
    index = (EXAMPLES / "README.md").read_text(encoding="utf-8")

    assert [name for name in NAMES if f"]({name}/)" not in index] == []


@pytest.mark.parametrize("name", NAMES)
def test_example_tests_pass(name: str) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", name],
        cwd=EXAMPLES,
        env=environment(),
        capture_output=True,
        text=True,
        timeout=TIMEOUT * 2,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


class Program(NamedTuple):
    module: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = {}  # noqa: RUF012  # never mutated
    exit_code: int = 0
    output: tuple[str, ...] = ()


def run(module: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", module, *args],
        cwd=EXAMPLES,
        env=environment(**(env or {})),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )


# The tmp_path of a test is not known here, so the databases of these programs go to a directory of their own
PROGRAMS = [
    Program("hello.main", output=("database: connected", "Hello, user-42!", "database: disconnected")),
    Program("plain_script.seed", env={"SQLITE_PATH": "{tmp}/seed.sqlite3"}, output=("seed: 5 customers in ",)),
    Program(
        "cli_job.export",
        ("--since", "2026-10-01"),
        env={"SQLITE_PATH": "{tmp}/orders.sqlite3"},
        output=("id,customer,amount,created", "4,ada,42.0,2026-10-01", "export: wrote 5 orders to stdout"),
    ),
    Program("cli_job.export", ("--since", "yesterday"), exit_code=2, output=("invalid date value: 'yesterday'",)),
    Program("http_job.versions", exit_code=2, output=("the following arguments are required: -p/--package",)),
    Program("settings.show", output=("show: one Settings for everyone: True",)),
    Program("settings.show", env={"POOL_SIZE": "0"}, exit_code=1, output=("POOL_SIZE must be at least 1, got 0",)),
    Program("not_singleton.main", output=("same HttpSession: False",)),
    Program("dataclass_clients.main", output=("placed order 1",)),
    Program("graph.show", output=("  Notifications --> Checkout",)),
    Program("graph.start", output=("Connecting client Checkout (7/8 connected)", "placed order 1")),
    Program("startup_failures.connect_error", output=("connected: False",)),
    Program(
        "startup_failures.connect_timeout",
        env={"CONNECT_TIMEOUT_SECONDS": "1"},
        output=("ConnectTimeoutError: Search did not connect within 1s (CONNECT_TIMEOUT_SECONDS)",),
    ),
    Program(
        "startup_failures.job",
        exit_code=1,
        output=(
            "postgres: disconnected",
            "ConnectError: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable",
        ),
    ),
    Program(
        "startup_failures.resolution",
        output=("CircularDependencyError: Circular dependency: Orders -> Payments -> Orders",),
    ),
    Program(
        "startup_failures.broken_job",
        exit_code=1,
        output=('"url" of "Cache.__init__" is str, which is not a client (resolving checkout -> Checkout -> Cache)',),
    ),
    Program(
        "hooks_metrics.export",
        env={"CONNECT_TIMEOUT_SECONDS": "1"},
        output=('job_exit_code{job="hooks_metrics.export.export"} 0', "more than half of CONNECT_TIMEOUT_SECONDS (1s)"),
    ),
    Program(
        "hooks_metrics.export",
        ("--limit", "three"),
        exit_code=2,
        output=('job_exit_code{job="hooks_metrics.export.export"} 2', "invalid int value: 'three'"),
    ),
    Program("testing.main", output=("registered user 4",)),
    Program(
        "testing.jobs.reminders", ("--limit", "1", "--dry-run"), output=("reminders: would remind bob@example.com",)
    ),
    Program(
        "service_layout.jobs.cleanup",
        ("--older-than-days", "0"),
        env={"SERVICE_DB": "{tmp}/service.db"},
        output=("cleanup: deleted 0 sent outbox rows older than 0 days",),
    ),
    Program(
        "background_tasks.worker",
        ("--fail-after", "2"),
        exit_code=1,
        output=("Background task refresh-cache failed", "ConnectionError: the settings service is unreachable"),
    ),
]


@pytest.mark.parametrize("program", PROGRAMS, ids=lambda p: " ".join([p.module, *p.args]))
def test_program(program: Program, tmp_path: Path) -> None:
    env = {key: value.format(tmp=tmp_path) for key, value in program.env.items()}

    result = run(program.module, *program.args, env=env)

    assert result.returncode == program.exit_code, result.stdout
    for line in program.output:
        assert line in result.stdout


class Running:
    """A long-running example: its output is read as it comes, so a test can wait for a line."""

    def __init__(self, module: str, *args: str, env: dict[str, str] | None = None) -> None:
        self.process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", module, *args],
            cwd=EXAMPLES,
            env=environment(**(env or {})),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.append(line)

    @property
    def output(self) -> str:
        return "".join(self.lines)

    def wait_for(self, text: str) -> None:
        deadline = time.monotonic() + TIMEOUT
        while text not in self.output:
            # The reader ends with the output of the process, so nothing more can come once it has ended;
            # the output is read once more, as the last line may have come just before the end
            finished = not self._reader.is_alive()
            if text in self.output:
                return
            if finished or time.monotonic() > deadline:
                pytest.fail(f"{text!r} never appeared in:\n{self.output}")
            time.sleep(0.05)

    def stop(self, signum: signal.Signals = signal.SIGINT) -> int:
        self.process.send_signal(signum)
        exit_code = self.process.wait(timeout=TIMEOUT)
        self._reader.join(timeout=TIMEOUT)
        return exit_code


@contextmanager
def running(module: str, *args: str, env: dict[str, str] | None = None) -> Generator[Running, None, None]:
    program = Running(module, *args, env=env)
    try:
        yield program
    finally:
        if program.process.poll() is None:
            program.process.kill()
            program.process.wait()


def test_sqlite_queue(tmp_path: Path) -> None:
    env = {"QUEUE_DB": str(tmp_path / "queue.db")}
    enqueued = run("sqlite_queue.enqueue", "--count", "3", env=env)
    assert enqueued.returncode == 0, enqueued.stdout
    assert "enqueue: 3 tasks pending" in enqueued.stdout

    with running("sqlite_queue.consumer", env=env) as consumer:
        consumer.wait_for("consumer: done task 3")
        assert consumer.stop() == 128 + signal.SIGINT

    assert "consumer: stopped" in consumer.output


def test_periodic_worker() -> None:
    with running("periodic_worker.cleanup", "--interval", "0.5") as cleanup:
        cleanup.wait_for("cleanup: deleted ['session-1'], 4 left")
        assert cleanup.stop(signal.SIGTERM) == 128 + signal.SIGTERM

    assert "cleanup: stopped" in cleanup.output


def test_background_tasks_worker() -> None:
    with running("background_tasks.worker") as worker:
        worker.wait_for("refresh: cache v2")
        assert worker.stop() == 128 + signal.SIGINT

    assert "worker: stopped" in worker.output
    assert "inbox: disconnected" in worker.output


def test_testing_worker() -> None:
    with running("testing.workers.signups") as signups:
        signups.wait_for("signups: registered user1@example.com as user 4")
        assert signups.stop() == 128 + signal.SIGINT

    assert "signups: stopped" in signups.output


def test_service_layout_worker(tmp_path: Path) -> None:
    with running("service_layout.workers.outbox", env={"SERVICE_DB": str(tmp_path / "service.db")}) as relay:
        relay.wait_for("outbox: relaying")
        assert relay.stop() == 128 + signal.SIGINT

    assert "outbox: stopped" in relay.output


@needs_nats
def test_nats_worker() -> None:
    with running("nats_worker.worker") as worker:
        worker.wait_for("worker: listening on nats_worker.orders")
        published = run("nats_worker.publish", "--count", "3")
        assert published.returncode == 0, published.stdout
        worker.wait_for("worker: order 3 saved")
        assert worker.stop() == 128 + signal.SIGINT

    assert "worker: order 1: 1 x book" in worker.output
    assert "worker: stopped" in worker.output


@needs_nats
def test_faststream_nats() -> None:
    with running("faststream_nats.app") as app:
        app.wait_for("FastStream app started successfully")
        published = run("faststream_nats.publish", "--count", "3", "--customer-id", "2")
        assert published.returncode == 0, published.stdout
        app.wait_for("receipt 3: bob has paid 29.97 in total")
        assert app.stop() == 0

    assert "ledger: disconnected" in app.output
