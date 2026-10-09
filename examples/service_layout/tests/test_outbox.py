import datetime
from pathlib import Path
from unittest.mock import AsyncMock, call

from nuke_di import Dependencies, Shutdown

from service_layout.clients import Broker, Orders, Outbox, Settings
from service_layout.jobs.cleanup import cleanup
from service_layout.workers.outbox import relay


async def test_relay_publishes_and_marks_sent(di: Dependencies, tmp_path: Path) -> None:
    """The worker publishes every pending row, finishes its batch after Shutdown, and marks the rows sent."""
    di.mock(Settings, Settings(db_path=str(tmp_path / "orders.db"), poll_seconds=0.01))
    broker = di.mock(Broker)
    shutdown = di.resolve(Shutdown)
    broker.publish.side_effect = lambda topic, payload: shutdown.set()  # what SIGTERM would do
    orders, outbox = di.resolve(Orders), di.resolve(Outbox)
    injected = di.inject(relay)

    async with di:
        await orders.create("book", 2)
        await orders.create("pen", 10)
        await injected()
        assert await outbox.pending(limit=10) == []

    assert broker.publish.await_args_list == [
        call("orders", {"event": "order_created", "order_id": 1}),
        call("orders", {"event": "order_created", "order_id": 2}),
    ]


async def test_cleanup() -> None:
    """The job is a plain coroutine on import: call it with a mock and a parameter."""
    outbox = AsyncMock()
    outbox.delete_sent.return_value = 3

    await cleanup(outbox, older_than_days=30)

    outbox.delete_sent.assert_awaited_once_with(datetime.timedelta(days=30))


async def test_cleanup_keeps_recent_and_pending_rows(di: Dependencies, tmp_path: Path) -> None:
    """With a real SQLite file: only the sent rows older than the cutoff are deleted."""
    di.mock(Settings, Settings(db_path=str(tmp_path / "orders.db")))
    orders, outbox = di.resolve(Orders), di.resolve(Outbox)

    async with di:
        for item in ["book", "pen", "lamp"]:
            await orders.create(item, 1)
        rows = await outbox.pending(limit=10)
        await outbox.mark_sent(rows[0].id)
        await outbox.mark_sent(rows[1].id)

        assert await outbox.delete_sent(datetime.timedelta(days=1)) == 0  # sent just now: kept
        assert await outbox.delete_sent(datetime.timedelta(0)) == 2  # the pending row is never deleted
        assert [row.payload["order_id"] for row in await outbox.pending(limit=10)] == [3]
