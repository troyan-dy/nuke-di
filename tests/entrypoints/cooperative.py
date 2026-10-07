from nuke_di import Client, Shutdown, worker


class Db(Client):
    async def disconnect(self) -> None:
        print("disconnected", flush=True)


@worker
async def cooperative(db: Db, shutdown: Shutdown) -> None:
    print("ready", flush=True)
    await shutdown.wait()
    print("stopped", flush=True)
