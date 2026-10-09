import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from nuke_di import Dependencies

from service_layout.api import app
from service_layout.clients import Settings


def test_create_and_get_order(global_di: Dependencies, tmp_path: Path) -> None:
    db_path = tmp_path / "orders.db"
    with global_di.override(Settings, Settings(db_path=str(db_path))), TestClient(app) as client:
        created = client.post("/orders", json={"item": "book", "quantity": 2})
        assert created.status_code == 201
        assert created.json() == {"id": 1, "item": "book", "quantity": 2}

        assert client.get("/orders/1").json() == {"id": 1, "item": "book", "quantity": 2}
        assert client.get("/orders/2").status_code == 404

    # The order and its event were written in one transaction; the worker has not relayed it yet
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT topic, payload, sent_at FROM outbox").fetchall()
    assert rows == [("orders", '{"event": "order_created", "order_id": 1}', None)]
