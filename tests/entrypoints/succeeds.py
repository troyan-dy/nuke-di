from nuke_di import Client, job


class Db(Client):
    async def connect(self) -> None:
        print("connected", flush=True)

    async def disconnect(self) -> None:
        print("disconnected", flush=True)


@job
async def succeeds(db: Db) -> None:
    print("ran", flush=True)


print("below the entrypoint", flush=True)
