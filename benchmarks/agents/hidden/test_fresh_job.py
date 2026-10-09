"""
fresh_job: `python sync_job.py --day 2026-10-01` runs, and Redis connects only after Postgres has connected.
"""

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from nuke_di import Dependencies


def test_runs_from_the_command_line() -> None:
    done = subprocess.run(
        [sys.executable, "sync_job.py", "--day", "2026-10-01"],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.index("postgres: connected") < done.stdout.index("redis: connected")


async def test_redis_connects_after_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("sync_job")
    events: list[str] = []

    def recording(cls: Any, name: str) -> None:
        original = cls.connect

        async def connect(self: Any) -> None:
            events.append(f"{name} start")
            await original(self)
            events.append(f"{name} done")

        monkeypatch.setattr(cls, "connect", connect)

    recording(module.Postgres, "postgres")
    recording(module.Redis, "redis")
    deps = Dependencies()
    deps.inject(module.sync)
    async with deps:
        pass

    assert events.index("postgres done") < events.index("redis start"), events
