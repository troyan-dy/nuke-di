import os

import httpx
from fastapi import FastAPI, HTTPException

from nuke_di import Client
from nuke_di.fastapi import setup


class OrderStore(Client):
    """Stands in for a Postgres pool: the orders live in a dict."""

    def __init__(self) -> None:
        self._orders: dict[int, str] = {}

    async def connect(self) -> None:
        self._orders = {1: "book", 2: "pen"}

    async def get(self, order_id: int) -> str | None:
        return self._orders.get(order_id)


class Payments(Client):
    """The payments service over one httpx.AsyncClient: created in connect(), closed in disconnect()."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        self._http = httpx.AsyncClient(base_url=os.environ["PAYMENTS_URL"], timeout=5)

    async def disconnect(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    async def paid(self, order_id: int) -> bool:
        assert self._http is not None
        response = await self._http.get(f"/payments/{order_id}")
        response.raise_for_status()
        return bool(response.json()["paid"])


app = FastAPI()
setup(app)


@app.get("/orders/{order_id}")
async def get_order(order_id: int, store: OrderStore, payments: Payments) -> dict[str, object]:
    item = await store.get(order_id)
    if item is None:
        raise HTTPException(status_code=404)
    return {"order_id": order_id, "item": item, "paid": await payments.paid(order_id)}
