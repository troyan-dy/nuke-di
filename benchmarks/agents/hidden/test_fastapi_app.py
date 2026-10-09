"""
protocol, startup, audit: the tasks on examples/fastapi_app keep the application working as before.
"""

import importlib

from fastapi.testclient import TestClient


def test_application_still_serves_users() -> None:
    app = importlib.import_module("fastapi_app.api").app

    with TestClient(app) as client:
        assert client.get("/users/1").json() == "alice"
        assert client.get("/users/99").status_code == 404
        assert client.get("/me", headers={"X-User-Id": "2"}).json() == "Hello, bob!"
