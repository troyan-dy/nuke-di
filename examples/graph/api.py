from graph.services import Checkout


async def place_order(user_id: int, checkout: Checkout) -> int:
    """The entrypoint whose tree the graph shows, e.g. a route of a web app."""
    return await checkout.place(user_id)
