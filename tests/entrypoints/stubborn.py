import asyncio
import sys

from nuke_di import Client, worker


class Db(Client):
    async def disconnect(self) -> None:
        sys.stdout.write("disconnected\n")
        sys.stdout.flush()


@worker
async def stubborn(db: Db) -> None:
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        sys.stdout.write("cancelled\n")
        sys.stdout.flush()
        raise
