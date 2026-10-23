import sys

from nuke_di import Client, job


class Db(Client):
    async def disconnect(self) -> None:
        sys.stdout.write("disconnected\n")
        sys.stdout.flush()


@job
async def fails(db: Db) -> None:
    raise RuntimeError("boom")
