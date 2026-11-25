import asyncio
import functools

from nats.aio.msg import Msg
from nuke_di import entrypoint
from pydantic import ValidationError

from nats_worker.clients import SUBJECT, Nats, Order, Orders

# Every replica subscribes in the same queue group, so NATS hands each message to one replica only
QUEUE_GROUP = "nats_worker"


async def process_order(msg: Msg, orders: Orders) -> None:
    try:
        order = Order.model_validate_json(msg.data)
    except ValidationError:
        print(f"worker: skipped an invalid message {msg.data!r}")
        return
    print(f"worker: order {order.id}: {order.quantity} x {order.product}")
    await asyncio.sleep(0.5)  # stand-in for the work
    await orders.save(order)
    print(f"worker: order {order.id} saved")


@entrypoint
async def consume(nats: Nats, orders: Orders) -> None:
    """Receive orders until the process is interrupted."""
    handler = functools.partial(process_order, orders=orders)
    subscription = await nats.connection.subscribe(SUBJECT, queue=QUEUE_GROUP, cb=handler)
    print(f"worker: listening on {SUBJECT}, queue group {QUEUE_GROUP}")
    try:
        await asyncio.Future()
    finally:
        # Finish deliveries while the connection and the other clients are still available.
        await subscription.drain()  # type: ignore[no-untyped-call]
        print("worker: stopped")
