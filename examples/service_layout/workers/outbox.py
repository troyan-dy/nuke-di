import asyncio
import contextlib
import signal
import sys

from nuke_di import Dependencies

from service_layout.clients import Broker, Outbox, Settings


async def relay(outbox: Outbox, broker: Broker, settings: Settings, stop: asyncio.Event) -> None:
    """Publish the pending outbox rows to the broker and mark them sent."""
    print("outbox: relaying")
    while not stop.is_set():
        rows = await outbox.pending(limit=100)
        for row in rows:
            await broker.publish(row.topic, row.payload)
            await outbox.mark_sent(row.id)
            print(f"outbox: published row {row.id} to {row.topic}")
        if not rows:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), settings.poll_seconds)
    print("outbox: stopped")


async def main() -> None:
    deps = Dependencies()
    run = deps.inject(relay)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    with contextlib.ExitStack() as signals:
        if sys.platform != "win32":
            loop.add_signal_handler(signal.SIGTERM, stop.set)
            signals.callback(loop.remove_signal_handler, signal.SIGTERM)
        async with deps:
            await run(stop=stop)


if __name__ == "__main__":
    asyncio.run(main())
