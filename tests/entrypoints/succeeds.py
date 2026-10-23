import sys

from nuke_di import Client, job


class Db(Client):
    async def connect(self) -> None:
        sys.stdout.write("connected\n")
        sys.stdout.flush()

    async def disconnect(self) -> None:
        sys.stdout.write("disconnected\n")
        sys.stdout.flush()


@job
async def succeeds(db: Db) -> None:
    sys.stdout.write("ran\n")
    sys.stdout.flush()


sys.stdout.write("below the entrypoint\n")
sys.stdout.flush()
