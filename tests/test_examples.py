"""
The examples in examples/ keep working: the tests of every example pass, and its programs run with the
exit code and the key lines of output their README shows. Each program runs in a process of its own, from
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
from xml.etree import ElementTree

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NAMES = sorted(path.parent.name for path in EXAMPLES.glob("*/__init__.py"))
TIMEOUT = 60
# What the examples and the library read from the environment: a test sets them, never whoever runs the tests
CONFIGURATION = frozenset(
    (
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
        "CONNECT_TIMEOUT_SECONDS",
        "DISCONNECT_DEADLINE_SECONDS",
        "DISCONNECT_TIMEOUT_SECONDS",
    )
)


def environment(**extra: str) -> dict[str, str]:
    # Coverage of the outer run must not follow into the example processes either
    settings = os.environ.items()
    env = {name: setting for name, setting in settings if inherited(name)}
    env.update(PYTHONUNBUFFERED="1", **extra)
    return env


def inherited(name: str) -> bool:
    return not name.startswith("COV_CORE_") and name not in CONFIGURATION


@pytest.fixture(scope="module")
def nats_available() -> None:
    try:
        socket.create_connection(("localhost", 4222), timeout=0.5).close()
    except OSError:
        pytest.skip("no NATS server on localhost:4222")


def test_every_example_has_a_readme() -> None:
    assert NAMES
    for name in NAMES:
        assert (EXAMPLES / name / "README.md").is_file(), name


def test_every_example_is_listed() -> None:
    index = (EXAMPLES / "README.md").read_text(encoding="utf-8")

    assert [name for name in NAMES if f"]({name}/)" not in index] == []


def test_example_tests_pass(tmp_path: Path) -> None:
    report = tmp_path / "examples.xml"
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", f"--junitxml={report}", *NAMES],
        cwd=EXAMPLES,
        env=environment(),
        capture_output=True,
        text=True,
        timeout=TIMEOUT * 2,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    cases = ElementTree.parse(report).iter("testcase")  # noqa: S314 - written by the pytest process above
    classes = (case.attrib["classname"] for case in cases)
    tested = {name.split(".", 1)[0] for name in classes}
    assert set(NAMES) <= tested


class Program(NamedTuple):
    module: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = {}  # noqa: RUF012  # never mutated
    exit_code: int = 0
    output: tuple[str, ...] = ()


def run(
    module: str,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    extra = env or {}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", module, *args],
        cwd=EXAMPLES,
        env=environment(**extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )


# The tmp_path of a test is not known here, so the databases of these programs go to a directory of their own
PROGRAMS = (
    Program("hello.main", output=("database: connected", "Hello, user-42!", "database: disconnected")),
    Program("plain_script.seed", env={"SQLITE_PATH": "{tmp}/seed.sqlite3"}, output=("seed: 5 customers in ",)),
    Program("http_job.versions", exit_code=2, output=("the following arguments are required: -p/--package",)),
    Program("settings.show", output=("show: one Settings for everyone: True",)),
    Program("settings.show", env={"POOL_SIZE": "0"}, exit_code=1, output=("POOL_SIZE must be at least 1, got 0",)),
    Program("dataclass_clients.main", output=("placed order 1",)),
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
        "mcp_server.ask",
        output=(
            "tool count_books(genre): How many books of a genre the shop has.",
            "count_books(genre='fantasy') = {'result': 3}",
            "database: disconnected",
        ),
    ),
    Program(
        "mcp_server.ask",
        ("fastmcp_server",),
        output=("tool recommend(): The book to start with.", "count_books(genre='fantasy') = {'result': 3}"),
    ),
    Program(
        "aiogram_bot.demo",
        output=("startup: bot 42, 0 users so far", "bot -> chat 8: 2 users so far", "database: disconnected, 2 users"),
    ),
    Program("testing.main", output=("registered user 4",)),
    Program(
        "taskiq_app.main",
        output=("send_report(1): sent to alice", "mail to admin: taskiq_app.tasks:send_reports finished"),
    ),
    Program("testing.jobs.reminders", output=("reminders: 2 users",)),
    Program(
        "service_layout.jobs.cleanup",
        env={"SERVICE_DB": "{tmp}/service.db"},
        output=("cleanup: deleted 0 sent outbox rows older than 7 days",),
    ),
)


def in_tmp(env: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """
    The variables of a program with {tmp} in their values made the tmp_path of the test.
    """
    settings = env.items()
    return {name: setting.format(tmp=tmp_path) for name, setting in settings}


def command_line(program: Program) -> str:
    return " ".join([program.module, *program.args])


@pytest.mark.parametrize("program", PROGRAMS, ids=command_line)
def test_program(program: Program, tmp_path: Path) -> None:
    env = in_tmp(program.env, tmp_path)
    completed = run(program.module, *program.args, env=env)

    assert completed.returncode == program.exit_code, completed.stdout
    for line in program.output:
        assert line in completed.stdout


class Running:
    """A long-running example: its output is read as it comes, so a test can wait for a line."""

    def __init__(
        self,
        module: str,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> None:
        extra = env or {}
        self.process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", module, *args],
            cwd=EXAMPLES,
            env=environment(**extra),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

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

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.append(line)


@contextmanager
def running(
    module: str,
    *args: str,
    env: dict[str, str] | None = None,
) -> Generator[Running, None, None]:
    program = Running(module, *args, env=env)
    try:
        yield program
    finally:
        if program.process.poll() is None:
            program.process.kill()
            program.process.wait()


def test_sqlite_queue(tmp_path: Path) -> None:
    env = {"QUEUE_DB": str(tmp_path / "queue.db")}
    enqueued = run("sqlite_queue.enqueue", env=env)
    assert enqueued.returncode == 0, enqueued.stdout
    assert "enqueue: 3 tasks pending" in enqueued.stdout

    consumed = run("sqlite_queue.consumer", env=env)
    assert consumed.returncode == 0, consumed.stdout
    assert "consumer: done task 3" in consumed.stdout
    assert "consumer: queue is empty" in consumed.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="the example handles SIGTERM on POSIX")
def test_service_layout_worker(tmp_path: Path) -> None:
    with running("service_layout.workers.outbox", env={"SERVICE_DB": str(tmp_path / "service.db")}) as relay:
        relay.wait_for("outbox: relaying")
        assert relay.stop(signal.SIGTERM) == 0

    assert "outbox: stopped" in relay.output


@pytest.mark.usefixtures("nats_available")
@pytest.mark.skipif(sys.platform == "win32", reason="the subprocess uses POSIX signal delivery")
def test_nats_worker() -> None:
    with running("nats_worker.worker") as worker:
        worker.wait_for("worker: listening on nats_worker.orders")
        published = run("nats_worker.publish")
        assert published.returncode == 0, published.stdout
        worker.wait_for("worker: order 3 saved")
        assert worker.stop() in {128 + signal.SIGINT, -signal.SIGINT}

    assert "worker: order 1: 1 x book" in worker.output
    assert "worker: stopped" in worker.output


@pytest.mark.usefixtures("nats_available")
@pytest.mark.skipif(sys.platform == "win32", reason="the subprocess uses POSIX signal delivery")
def test_faststream_nats() -> None:
    with running("faststream_nats.app") as app:
        app.wait_for("FastStream app started successfully")
        published = run("faststream_nats.publish", "--count", "3", "--customer-id", "2")
        assert published.returncode == 0, published.stdout
        app.wait_for("receipt 3: bob has paid 29.97 in total")
        assert app.stop() == 0

    assert "ledger: disconnected" in app.output
