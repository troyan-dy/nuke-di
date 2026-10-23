from typing import Annotated

from nuke_di import Option, job

from nats_worker.clients import SUBJECT, Nats, Order

PRODUCTS = ("book", "lamp", "mug")


@job
async def publish(
    nats: Nats,
    count: Annotated[int, Option(help="How many orders to publish", short="n")] = 3,
) -> None:
    """Publish test orders to nats_worker.orders."""
    for number in range(1, count + 1):
        product = PRODUCTS[(number - 1) % len(PRODUCTS)]
        order = Order(id=number, product=product, quantity=number)
        await nats.connection.publish(SUBJECT, order.model_dump_json().encode())
        print(f"publish: order {order.id}")
