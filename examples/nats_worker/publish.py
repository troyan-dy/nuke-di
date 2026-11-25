from nuke_di import entrypoint

from nats_worker.clients import SUBJECT, Nats, Order

PRODUCTS = ("book", "lamp", "mug")


@entrypoint
async def publish(nats: Nats, count: int = 3) -> None:
    """Publish test orders to nats_worker.orders."""
    for number in range(1, count + 1):
        product = PRODUCTS[(number - 1) % len(PRODUCTS)]
        order = Order(id=number, product=product, quantity=number)
        await nats.connection.publish(SUBJECT, order.model_dump_json().encode())
        print(f"publish: order {order.id}")
