"""
What the clients of the service hand out: an order, and a row of the outbox.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


class Order(BaseModel):
    id: int
    product: str
    quantity: int


@dataclass(frozen=True)
class OutboxRow:
    id: int
    topic: str
    payload: dict[str, Any]
