from decimal import Decimal

from pydantic import BaseModel


class Order(BaseModel):
    order_id: int
    amount: Decimal


class Receipt(BaseModel):
    order_id: int
    customer: str
    total: Decimal
