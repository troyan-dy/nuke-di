import asyncio
from typing import Annotated

from faststream import Depends, FastStream, Header
from faststream.nats import NatsBroker
from nuke_di.faststream import setup

from faststream_nats.clients import Database, Ledger
from faststream_nats.messages import Order, Receipt

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
setup(app)  # clients connect before the broker starts, disconnect after it stops


async def customer_name(customer_id: Annotated[int, Header("customer-id")], db: Database) -> str:
    # A dependency takes a header of the message and a client by type hint
    return await db.fetch_customer(customer_id)


@broker.subscriber("faststream_nats.orders")
@broker.publisher("faststream_nats.receipts")  # the return value is published there
async def handle_order(order: Order, ledger: Ledger, customer: Annotated[str, Depends(customer_name)]) -> Receipt:
    total = ledger.record(customer, order.amount)
    print(f"order {order.order_id}: {customer} paid {order.amount}")
    return Receipt(order_id=order.order_id, customer=customer, total=total)


@broker.subscriber("faststream_nats.receipts")
async def print_receipt(receipt: Receipt) -> None:
    print(f"receipt {receipt.order_id}: {receipt.customer} has paid {receipt.total} in total")


if __name__ == "__main__":
    asyncio.run(app.run())
