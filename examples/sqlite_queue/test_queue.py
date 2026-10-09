from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from nuke_di import Dependencies, Shutdown

from sqlite_queue.clients import Database, Task, TaskQueue
from sqlite_queue.consumer import consume
from sqlite_queue.enqueue import enqueue


@pytest.fixture(autouse=True)
def queue_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUEUE_DB", str(tmp_path / "queue.db"))


async def test_claim_takes_each_task_once(di: Dependencies) -> None:
    queue = di.resolve(TaskQueue)
    async with di:
        first = await queue.put("email", "email-1")
        second = await queue.put("email", "email-2")

        assert await queue.claim() == Task(first, "email", "email-1")
        assert await queue.claim() == Task(second, "email", "email-2")
        assert await queue.claim() is None


async def test_enqueue_then_consume(di: Dependencies) -> None:
    # The real Database and TaskQueue on a temporary file, wired by the container
    shutdown, queue, db = di.resolve(Shutdown), di.resolve(TaskQueue), di.resolve(Database)
    add, run = di.inject(enqueue), di.inject(consume)
    claim = queue.claim

    async def claim_until_empty() -> Task | None:
        task = await claim()
        if task is None:
            shutdown.set()  # the queue is drained: stop as SIGTERM would
        return task

    queue.claim = claim_until_empty  # type: ignore[method-assign]
    async with di:
        await add(count=2, kind="report")
        await run(poll_interval=3600)

        assert db.connection.execute("SELECT status FROM tasks").fetchall() == [("done",), ("done",)]


async def test_consumer_finishes_the_task_on_shutdown() -> None:
    queue, shutdown = AsyncMock(spec=TaskQueue), Shutdown()

    async def claim() -> Task:
        shutdown.set()  # what SIGTERM would do while the task is being processed
        return Task(1, "email", "email-1")

    queue.claim.side_effect = claim

    await consume(queue, shutdown)

    queue.done.assert_awaited_once_with(1)


async def test_empty_queue_wakes_up_on_shutdown() -> None:
    queue, shutdown = AsyncMock(spec=TaskQueue), Shutdown()

    async def claim() -> None:
        shutdown.set()
        return None

    queue.claim.side_effect = claim

    # A poll interval of an hour: the test only finishes if Shutdown cuts the wait short
    await consume(queue, shutdown, poll_interval=3600)

    queue.done.assert_not_awaited()
