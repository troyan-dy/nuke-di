"""
fresh_fastapi: GET /orders/{order_id} answers from the store and the payments service, over one shared AsyncClient.
"""

import importlib
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient


@dataclass
class Payments:
    """
    What the application asked of the payments service, and the AsyncClients it created.
    """

    requested: list[str] = field(default_factory=list)
    created: list[httpx.AsyncClient] = field(default_factory=list)


@pytest.fixture
def payments(monkeypatch: pytest.MonkeyPatch) -> Payments:
    monkeypatch.setenv("PAYMENTS_URL", "http://payments.test")
    seen = Payments()
    original_init = httpx.AsyncClient.__init__

    def counting_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        seen.created.append(self)
        original_init(self, *args, **kwargs)

    async def answer(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        seen.requested.append(str(request.url))
        return httpx.Response(200, json={"paid": True})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", counting_init)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", answer)
    return seen


def test_orders(payments: Payments) -> None:
    app = importlib.import_module("app.main").app

    with TestClient(app) as client:
        first, second, missing = responses(client, 1, 2, 9)

    assert first.status_code == 200, first.text
    assert [first.json(), second.json()] == [
        {"order_id": 1, "item": "book", "paid": True},
        {"order_id": 2, "item": "pen", "paid": True},
    ]
    assert missing.status_code == 404
    assert payments.requested[:2] == ["http://payments.test/payments/1", "http://payments.test/payments/2"]
    # One AsyncClient for the application, not one per request
    assert len(payments.created) == 1


def responses(client: TestClient, *order_ids: int) -> list[Any]:
    """
    The responses of GET /orders/{order_id}, one request each, in order.
    """
    found = []
    for order_id in order_ids:
        found.append(client.get(f"/orders/{order_id}"))
    return found
