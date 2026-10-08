"""
End-to-end: entrypoint modules run as real processes with `python -m`.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is POSIX only")


def start(module: str, *args: str, **env: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - fixed interpreter and module from this test suite
        [sys.executable, "-m", f"tests.entrypoints.{module}", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, **env},
    )


def wait_ready(process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"


def finish(process: subprocess.Popen[str]) -> tuple[int, str, str]:
    stdout, stderr = process.communicate(timeout=10)
    return process.returncode, stdout, stderr


def test_job_succeeds() -> None:
    code, stdout, _ = finish(start("succeeds"))

    assert code == 0
    assert stdout.split() == ["connected", "ran", "disconnected"]


def test_job_fails() -> None:
    code, stdout, stderr = finish(start("fails"))

    assert code == 1
    assert stdout.split() == ["disconnected"]
    assert "RuntimeError: boom" in stderr


@posix_only
def test_cooperative_worker_stops_on_sigterm() -> None:
    process = start("cooperative")
    wait_ready(process)
    process.send_signal(signal.SIGTERM)
    code, stdout, _ = finish(process)

    assert code == 128 + signal.SIGTERM
    assert stdout.split() == ["stopped", "disconnected"]


@posix_only
def test_stubborn_worker_is_cancelled_after_grace() -> None:
    process = start("stubborn", SHUTDOWN_GRACE_SECONDS="0.1")
    wait_ready(process)
    process.send_signal(signal.SIGTERM)
    code, stdout, stderr = finish(process)

    assert code == 128 + signal.SIGTERM
    assert stdout.split() == ["cancelled", "disconnected"]
    assert "did not stop within 0.1s" in stderr


@posix_only
def test_second_sigint_cancels_immediately() -> None:
    process = start("stubborn")
    wait_ready(process)
    process.send_signal(signal.SIGINT)
    # Two signals sent back to back may be delivered as one
    time.sleep(0.2)
    process.send_signal(signal.SIGINT)
    code, stdout, _ = finish(process)

    assert code == 128 + signal.SIGINT
    assert stdout.split() == ["cancelled", "disconnected"]


def test_failing_background_task_fails_worker() -> None:
    code, _, stderr = finish(start("background_fails"))

    assert code == 1
    assert "background boom" in stderr


def test_parameters_reach_job() -> None:
    code, stdout, _ = finish(start("parameters", "-d", "2026-10-01", "--tables", "a", "--tables", "b", "--dry-run"))

    assert code == 0
    assert stdout.splitlines() == ["connected", "2026-10-01 DIFF ['a', 'b'] True"]


def test_help() -> None:
    code, stdout, _ = finish(start("parameters", "--help"))

    assert code == 0
    assert stdout.startswith("usage: python -m tests.entrypoints.parameters")
    assert "Day to sync" in stdout


def test_invalid_parameter() -> None:
    code, stdout, stderr = finish(start("parameters", "--day", "nope"))

    assert code == 2
    assert stdout == ""
    assert "usage: python -m tests.entrypoints.parameters" in stderr
    assert "error: argument -d/--day: invalid date value: 'nope'" in stderr


def test_unknown_argument_of_job_without_parameters() -> None:
    code, stdout, stderr = finish(start("succeeds", "--bogus"))

    assert code == 2
    assert stdout == ""
    assert "error: unrecognized arguments: --bogus" in stderr
