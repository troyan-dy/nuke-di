import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from nuke_di import DI, Dependencies

from fastapi_app.api import app
from fastapi_app.clients import Database, UserCache


class FakeDatabase(Database):
    def __init__(self) -> None:
        super().__init__()
        self.users = {7: "tester"}

    async def fetch_users(self) -> dict[int, str]:
        return dict(self.users)


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Replaced before TestClient starts the app; a Replacement is never connected
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        yield client


def test_get_user(client: TestClient) -> None:
    assert client.get("/users/7").json() == "tester"
    assert client.get("/users/1").status_code == 404


def test_me(client: TestClient) -> None:
    assert client.get("/me", headers={"X-User-Id": "7"}).json() == "Hello, tester!"
    assert client.get("/me", headers={"X-User-Id": "1"}).status_code == 401


def test_websocket(client: TestClient) -> None:
    with client.websocket_connect("/ws/users") as ws:
        ws.send_text("7")
        assert ws.receive_text() == "tester"
        ws.send_text("1")
        assert ws.receive_text() == "not found"


async def test_cache_refreshes(di: Dependencies, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(UserCache, "refresh_seconds", 0.01)
    db = FakeDatabase()
    di.mock(Database, db)
    cache = di.resolve(UserCache)
    async with di:
        assert cache.get(8) is None
        db.users[8] = "newcomer"
        await asyncio.sleep(0.05)
        assert cache.get(8) == "newcomer"
