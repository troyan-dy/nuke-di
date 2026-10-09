from collections.abc import Iterator

import pytest
from nuke_di import DI, Dependencies
from starlette.testclient import TestClient

from starlette_app.app import app
from starlette_app.clients import Database


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str | None:
        return "tester" if user_id == 7 else None


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Replaced before TestClient starts the app; a Replacement is never connected
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        yield client


def test_greet_user(client: TestClient) -> None:
    assert client.get("/users/7").text == "Hello, tester!"
    assert client.get("/users/1").status_code == 404


def test_me(client: TestClient) -> None:
    assert client.get("/me", headers={"X-User-Id": "7"}).text == "You are tester"
    assert client.get("/me").status_code == 401


def test_restarts_with_fresh_clients(global_di: Dependencies, capsys: pytest.CaptureFixture[str]) -> None:
    # The handlers are bound again on every startup, so a second start gets connected clients
    for _ in range(2):
        with TestClient(app) as client:
            assert client.get("/users/1").text == "Hello, alice!"
    assert capsys.readouterr().out.count("database: connected") == 2
