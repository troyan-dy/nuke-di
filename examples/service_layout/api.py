from fastapi import FastAPI, HTTPException, status
from nuke_di.fastapi import setup
from pydantic import BaseModel

from service_layout.clients import Orders
from service_layout.models import Order

app = FastAPI()
setup(app)  # before the routes: the clients they take connect on startup, disconnect on shutdown


class NewOrder(BaseModel):
    product: str
    quantity: int = 1


@app.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_order(new: NewOrder, orders: Orders) -> Order:
    return await orders.create(new.product, new.quantity)


@app.get("/orders/{order_id}")
async def get_order(order_id: int, orders: Orders) -> Order:
    order = await orders.get(order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"order {order_id} not found")
    return order


@app.get("/health")
async def health() -> str:
    return "ok"
