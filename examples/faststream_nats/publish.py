from decimal import Decimal
from typing import Annotated

from faststream.nats import NatsBroker
from nuke_di import Client, Option, job

from faststream_nats.messages import Order


class Nats(Client):
    """
    A NATS connection that only publishes: a third-party object wrapped as a client.
    """

    def __init__(self) -> None:
        self._broker = NatsBroker("nats://localhost:4222")

    async def connect(self) -> None:
        await self._broker.connect()
        print("nats: connected")

    async def disconnect(self) -> None:
        await self._broker.stop()
        print("nats: disconnected")

    async def send_order(self, order: Order, customer_id: int) -> None:
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
