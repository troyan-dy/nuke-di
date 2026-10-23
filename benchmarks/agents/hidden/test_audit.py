"""
audit: every GET /users/{user_id} is recorded with a request id of its own, and GET /audit lists the records.
"""

import importlib
import uuid
from typing import Any

from fastapi.testclient import TestClient


def test_every_request_is_recorded_with_its_own_id() -> None:
    app = importlib.import_module("fastapi_app.api").app

    with TestClient(app) as client:
        users = answers(client, 1, 1, 2)
        records = client.get("/audit").json()

    assert users == ["alice", "alice", "bob"]
    assert [record["user_id"] for record in records] == [1, 1, 2]
    ids = [str(record["request_id"]) for record in records]
    assert len(set(ids)) == 3
    assert all(uuid.UUID(request_id).version == 4 for request_id in ids), ids


def answers(client: TestClient, *user_ids: int) -> list[Any]:
    """
    The answers of GET /users/{user_id}, one request each, in order.
    """
    found = []
    for user_id in user_ids:
        found.append(client.get(f"/users/{user_id}").json())
    return found
