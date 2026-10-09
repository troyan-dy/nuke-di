import os

import nats
from nats.aio.client import Client as NatsConnection
from nuke_di import Client
from pydantic import BaseModel

SUBJECT = "nats_worker.orders"


class Order(BaseModel):
    id: int
    item: str
    quantity: int


class Nats(Client):
    """
    A NATS connection: the servers are read in __init__, the connection is made in connect().
    """

    def __init__(self) -> None:
        self.servers = os.environ.get("NATS_URL", "nats://localhost:4222")
        self._connection: NatsConnection | None = None

    async def connect(self) -> None:
        self._connection = await nats.connect(self.servers, error_cb=self._on_error)
        print("nats: connected")

    async def disconnect(self) -> None:
        if self._connection is not None:
            # drain(): flush what was published, let the subscriptions finish their messages, then close
            await self._connection.drain()
            self._connection = None
        print("nats: disconnected")

    async def _on_error(self, error: Exception) -> None:
        if self._connection is None:
            # The first connection: fail at once, instead of nats-py retrying it for two minutes
            raise error
        # Later errors: nats-py reconnects on its own
        print(f"nats: {error!r}")

    @property
    def connection(self) -> NatsConnection:
        if self._connection is None:
            raise RuntimeError("Nats is not connected")
        return self._connection


class Orders(Client):
    """
    Where a handled order goes; a stand-in for a database.
    """

    def __init__(self) -> None:
        self.saved: list[Order] = []

    async def save(self, order: Order) -> None:
        self.saved.append(order)
