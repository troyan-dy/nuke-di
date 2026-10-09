import itertools

from nuke_di import Client


class Inbox(Client):
    """
    An endless in-memory queue of demo messages.
    """

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.received = 0

    async def connect(self) -> None:
        print("inbox: connected")

    async def disconnect(self) -> None:
        print("inbox: disconnected")

    async def get(self) -> str:
        self.received += 1
        return f"message-{next(self._ids)}"


class Cache(Client):
    """
    A copy of remote settings that is refreshed in the background.
    """

    def __init__(self) -> None:
        self.version = 0

    async def connect(self) -> None:
        await self.refresh()
        print(f"cache: connected, v{self.version}")

    async def disconnect(self) -> None:
        print("cache: disconnected")

    async def refresh(self) -> None:
        self.version += 1
