"""
The clients the cases of the contract take; they record what they do in the events of the running case.
"""

from nuke_di.integration.testing.outcomes import _events
from nuke_di.types import Client


class Store(Client):
    async def connect(self) -> None:
        _events.append("store: connected")

    async def disconnect(self) -> None:
        _events.append("store: disconnected")

    def name(self) -> str:
        return "store"


class ReplacementStore(Store):
    def name(self) -> str:
        return "replacement"


class Greeter(Client):
    def __init__(self, store: Store) -> None:
        self.store = store

    def greet(self) -> str:
        name = self.store.name()
        return f"Hello, {name}!"


class Broken(Client):
    async def connect(self) -> None:
        raise OSError("unreachable")
