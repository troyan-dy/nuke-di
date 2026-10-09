"""
fresh_fastapi: GET /orders/{order_id} answers from the store and the payments service, over one shared AsyncClient.
"""

import importlib
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient


def test_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYMENTS_URL", "http://payments.test")
    requested: list[str] = []
    created: list[httpx.AsyncClient] = []
    original_init = httpx.AsyncClient.__init__

    def counting_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        created.append(self)
        original_init(self, *args, **kwargs)

    async def payments(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json={"paid": True})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", counting_init)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", payments)
    app = importlib.import_module("app.main").app

    with TestClient(app) as client:
        first = client.get("/orders/1")
        second = client.get("/orders/2")
        missing = client.get("/orders/9")

    assert first.status_code == 200, first.text
    assert first.json() == {"order_id": 1, "item": "book", "paid": True}
    assert second.json() == {"order_id": 2, "item": "pen", "paid": True}
    assert missing.status_code == 404
    assert requested[:2] == ["http://payments.test/payments/1", "http://payments.test/payments/2"]
    # One AsyncClient for the application, not one per request
    assert len(created) == 1
