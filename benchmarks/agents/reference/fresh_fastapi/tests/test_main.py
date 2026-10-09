from app.main import Payments, app
from fastapi.testclient import TestClient

from nuke_di import DI


def test_order() -> None:
    with DI.override(Payments) as payments, TestClient(app) as client:
        payments.paid.return_value = True
        assert client.get("/orders/1").json() == {"order_id": 1, "item": "book", "paid": True}
        assert client.get("/orders/9").status_code == 404
