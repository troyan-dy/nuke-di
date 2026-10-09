import os
from decimal import Decimal
from typing import Annotated

from faststream.nats import NatsBroker
from nuke_di import Client, Option, job

from faststream_nats.messages import Order


class Nats(Client):
    """
    A NATS connection that only publishes: a third-party object wrapped as a client. The servers are read in
    __init__, the broker is made and connected in connect().
    """

    def __init__(self) -> None:
        self.servers = os.environ.get("NATS_URL", "nats://localhost:4222")
        self._broker: NatsBroker | None = None

    async def connect(self) -> None:
        # A one-off publisher needs no reconnects: one more attempt, then a NATS that is down fails the job,
        # instead of nats-py trying 60 times until CONNECT_TIMEOUT_SECONDS
        broker = NatsBroker(self.servers, max_reconnect_attempts=1)
        await broker.connect()
        self._broker = broker
        print("nats: connected")

    async def disconnect(self) -> None:
        if self._broker is not None:
            await self._broker.stop()
            self._broker = None
        print("nats: disconnected")

    async def send_order(self, order: Order, customer_id: int) -> None:
        if self._broker is None:
            raise RuntimeError("Nats is not connected")
        await self._broker.publish(order, "faststream_nats.orders", headers={"customer-id": str(customer_id)})


@job
async def publish(
    nats: Nats,
    count: Annotated[int, Option(help="How many orders to send")] = 3,
    customer_id: Annotated[int, Option(help="The customer-id header")] = 1,
) -> None:
    """Send test orders to the faststream_nats.orders subject."""
    for order_id in range(1, count + 1):
        await nats.send_order(Order(order_id=order_id, amount=Decimal("9.99")), customer_id)
        print(f"published order {order_id}")
