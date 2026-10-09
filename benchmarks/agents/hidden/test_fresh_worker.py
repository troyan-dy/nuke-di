"""
fresh_worker: `consume` stops after the current message on a Shutdown, and `python consumer.py` exits on SIGTERM.
"""

import asyncio
import importlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from nuke_di import Dependencies, Shutdown


async def test_stops_after_the_current_message() -> None:
    module = importlib.import_module("consumer")
    deps = Dependencies()
    queue = deps.mock(module.Queue)
    shutdown = deps.resolve(Shutdown)
    messages = iter(["m1", "m2", "m3"])

    async def get() -> str:
        message = next(messages)
        if message == "m2":
            shutdown.set()  # what SIGTERM would do, while m2 is in hand
        return message

    queue.get.side_effect = get
    injected = deps.inject(module.consume)
    async with deps:
        await asyncio.wait_for(injected(), timeout=5)

    assert queue.get.await_count == 2


def test_exits_on_sigterm() -> None:
    process = subprocess.Popen(
        [sys.executable, "consumer.py"],
        cwd=Path.cwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "SHUTDOWN_GRACE_SECONDS": "3"},
    )
    time.sleep(1.5)
    assert process.poll() is None, "the process ended on its own"
    process.send_signal(signal.SIGTERM)
    try:
        output, _ = process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        raise AssertionError("still running 8 s after SIGTERM") from None

    assert "Traceback" not in output, output
