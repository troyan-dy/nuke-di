"""
audit: every GET /users/{user_id} is recorded with a request id of its own, and GET /audit lists the records.
"""

import importlib
import uuid

from fastapi.testclient import TestClient


def test_every_request_is_recorded_with_its_own_id() -> None:
    app = importlib.import_module("fastapi_app.api").app

    with TestClient(app) as client:
        assert client.get("/users/1").json() == "alice"
        assert client.get("/users/1").json() == "alice"
        assert client.get("/users/2").json() == "bob"
        records = client.get("/audit").json()

    assert [record["user_id"] for record in records] == [1, 1, 2]
    ids = [str(record["request_id"]) for record in records]
    assert len(set(ids)) == 3
    for request_id in ids:
        assert uuid.UUID(request_id).version == 4
