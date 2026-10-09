"""
startup: a Database that cannot connect fails the startup at once; nuke-di leaves the retry to the orchestrator.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


def test_unreachable_database_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    app = importlib.import_module("fastapi_app.api").app
    database = importlib.import_module("fastapi_app.clients").Database
    calls: list[object] = []

    async def unreachable(self: object) -> None:
        calls.append(self)
        raise ConnectionRefusedError("database is unreachable")

    monkeypatch.setattr(database, "connect", unreachable)

    with pytest.raises(Exception), TestClient(app):  # noqa: B017 - whatever the startup raises
        pass

    assert len(calls) == 1, f"Database.connect() was called {len(calls)} times: a retry"
