import asyncio

from nuke_di import Client, worker


class Db(Client):
    async def disconnect(self) -> None:
        print("disconnected", flush=True)


@worker
async def stubborn(db: Db) -> None:
    print("ready", flush=True)
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        print("cancelled", flush=True)
        raise
