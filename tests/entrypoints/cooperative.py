import sys

from nuke_di import Client, Shutdown, worker


class Db(Client):
    async def disconnect(self) -> None:
        sys.stdout.write("disconnected\n")
        sys.stdout.flush()


@worker
async def cooperative(db: Db, shutdown: Shutdown) -> None:
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    await shutdown.wait()
    sys.stdout.write("stopped\n")
    sys.stdout.flush()
