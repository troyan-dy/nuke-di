import asyncio
import functools

from nats.aio.msg import Msg
from nuke_di import Shutdown, worker
from pydantic import ValidationError

from nats_worker.clients import SUBJECT, Nats, Order, Orders

# Every replica subscribes in the same queue group, so NATS hands each message to one replica only
QUEUE_GROUP = "nats_worker"


async def handle(msg: Msg, orders: Orders) -> None:
    try:
        order = Order.model_validate_json(msg.data)
    except ValidationError:
        print(f"worker: skipped an invalid message {msg.data!r}")
        return
    print(f"worker: order {order.id}: {order.quantity} x {order.item}")
    await asyncio.sleep(0.5)  # the actual work
    await orders.save(order)
    print(f"worker: order {order.id} saved")


@worker
async def consume(nats: Nats, orders: Orders, shutdown: Shutdown) -> None:
    """Handle the orders published on nats_worker.orders until SIGTERM or SIGINT."""
    handler = functools.partial(handle, orders=orders)
    subscription = await nats.connection.subscribe(SUBJECT, queue=QUEUE_GROUP, cb=handler)
    print(f"worker: listening on {SUBJECT}, queue group {QUEUE_GROUP}")
    await shutdown.wait()
    # Stop receiving and finish the messages already delivered to this replica
    await subscription.drain()  # type: ignore[no-untyped-call]  # nats-py leaves drain() unannotated
    print("worker: stopped")
