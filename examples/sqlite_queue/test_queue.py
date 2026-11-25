from pathlib import Path

import pytest
from nuke_di import Dependencies

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
    db = di.resolve(Database)
    add, drain = di.inject(enqueue), di.inject(consume)

    async with di:
        await add(count=2, kind="report")
        await drain()

        assert db.connection.execute("SELECT status FROM tasks").fetchall() == [("done",), ("done",)]
