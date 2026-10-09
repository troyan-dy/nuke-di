import asyncio
import contextlib
from typing import Annotated

from nuke_di import Option, Shutdown, worker

from periodic_worker.clients import Sessions


@worker
async def cleanup(
    sessions: Sessions,
    shutdown: Shutdown,
    interval: Annotated[float, Option(help="Seconds between two cleanups")] = 5.0,
) -> None:
    """Delete the expired sessions every --interval seconds until SIGTERM or SIGINT."""
    while not shutdown.is_set():
        expired = await sessions.delete_expired()
        print(f"cleanup: deleted {expired}, {len(sessions)} left")
        # Sleep until the next run, but wake up at once on Shutdown
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=interval)
    print("cleanup: stopped")
