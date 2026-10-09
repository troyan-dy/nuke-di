import asyncio
import contextlib

from nuke_di import Shutdown, worker

from service_layout.clients import Broker, Outbox, Settings


@worker
async def relay(outbox: Outbox, broker: Broker, settings: Settings, shutdown: Shutdown) -> None:
    """Publish the pending outbox rows to the broker and mark them sent."""
    print("outbox: relaying")
    while not shutdown.is_set():
        rows = await outbox.pending(limit=100)
        for row in rows:  # a started batch is finished: every row is published once and marked sent
            await broker.publish(row.topic, row.payload)
            await outbox.mark_sent(row.id)
            print(f"outbox: published order {row.payload['order_id']}")
        if not rows:
            # Sleep until the next poll, but wake up at once on SIGTERM
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown.wait(), settings.poll_seconds)
    print("outbox: stopped")
