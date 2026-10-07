from nuke_di import Client, job


class Db(Client):
    async def disconnect(self) -> None:
        print("disconnected", flush=True)


@job
async def fails(db: Db) -> None:
    raise RuntimeError("boom")
