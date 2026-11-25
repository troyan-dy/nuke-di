import argparse
import asyncio
import os
from decimal import Decimal

from faststream.nats import NatsBroker
from nuke_di import Client, Dependencies

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


async def publish(nats: Nats, count: int, customer_id: int) -> None:
    """Send test orders to the faststream_nats.orders subject."""
    for order_id in range(1, count + 1):
        await nats.send_order(Order(order_id=order_id, amount=Decimal("9.99")), customer_id)
        print(f"published order {order_id}")


async def main(count: int, customer_id: int) -> None:
    deps = Dependencies()
    send = deps.inject(publish)
    async with deps:
        await send(count=count, customer_id=customer_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish test orders to FastStream.")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--customer-id", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.count, args.customer_id))
