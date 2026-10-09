import asyncio
import contextlib
from unittest.mock import MagicMock

import pytest
from nuke_di import BackgroundTasks, Shutdown

from background_tasks.clients import Cache, Inbox
from background_tasks.worker import heartbeat, process, refresh_cache


async def test_spawns_the_background_tasks() -> None:
    tasks, shutdown = BackgroundTasks(), Shutdown()
    spawn = MagicMock(side_effect=tasks.spawn)
    tasks.spawn = spawn  # type: ignore[method-assign]
    shutdown.set()  # no message is processed: only the startup is under test

    await process(Inbox(), Cache(), tasks, shutdown)

    assert [call.kwargs["name"] for call in spawn.call_args_list] == ["heartbeat", "refresh-cache"]
    await tasks.stop()  # what disconnect() does


async def test_heartbeat_reports_the_received_messages(capsys: pytest.CaptureFixture[str]) -> None:
    inbox = Inbox()
    await inbox.get()
    task = asyncio.create_task(heartbeat(inbox, interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert "heartbeat: alive, 1 messages received" in capsys.readouterr().out


async def test_refresh_fails_after_n_refreshes() -> None:
    cache = Cache()

    with pytest.raises(ConnectionError):
        await refresh_cache(cache, interval=0, fail_after=2)

    assert cache.version == 2


async def test_a_failed_task_is_reported() -> None:
    tasks = BackgroundTasks()
    failures: list[BaseException] = []
    tasks.watch(failures.append)  # how a worker run learns that it must fail

    task = tasks.spawn(refresh_cache(Cache(), interval=0, fail_after=0), name="refresh-cache")
    await asyncio.wait({task})

    assert [type(exc) for exc in failures] == [ConnectionError]
