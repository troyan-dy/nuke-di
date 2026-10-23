"""
An application: clients whose connect() and disconnect() take the time a real connection does, in a tree with
slack between its branches, so the wall time is what a startup and a shutdown take, against the critical path: a
client that waited for more than its own dependencies would show (#28). These are its connections; its services
are in suite/application.py.
"""

import asyncio

from nuke_di import Client


class Sleeper(Client):
    """
    A client whose connect() and disconnect() sleep for a fixed time.
    """

    connect_seconds: float = 0
    disconnect_seconds: float = 0
    # The clients this one depends on, for the schedule without the container
    dependencies: tuple["Sleeper", ...] = ()

    async def connect(self) -> None:
        await asyncio.sleep(self.connect_seconds)

    async def disconnect(self) -> None:
        await asyncio.sleep(self.disconnect_seconds)


class Settings(Sleeper):
    connect_seconds = 0.001


class Postgres(Sleeper):
    connect_seconds = 0.03
    disconnect_seconds = 0.005

    def __init__(self, settings: Settings) -> None:
        self.dependencies = (settings,)


class Redis(Sleeper):
    connect_seconds = 0.02
    disconnect_seconds = 0.002

    def __init__(self, settings: Settings) -> None:
        self.dependencies = (settings,)


class Kafka(Sleeper):
    connect_seconds = 0.06
    disconnect_seconds = 0.01

    def __init__(self, settings: Settings) -> None:
        self.dependencies = (settings,)
