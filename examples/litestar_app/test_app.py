from collections.abc import Iterator

import pytest
from litestar import Litestar
from litestar.testing import TestClient
from nuke_di import DI

from litestar_app.app import app
from litestar_app.clients import Database


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str | None:
        return "tester" if user_id == 7 else None

    async def list_users(self) -> list[str]:
        return ["tester"]


@pytest.fixture
def client() -> Iterator[TestClient[Litestar]]:
    # Replaced before TestClient starts the app; a Replacement is never connected
    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        yield client


def test_controller(client: TestClient[Litestar]) -> None:
    assert client.get("/users").json() == ["tester"]
    assert client.get("/users/7").text == "Hello, tester!"
    assert client.get("/users/1").status_code == 404


def test_me(client: TestClient[Litestar]) -> None:
    assert client.get("/me", headers={"X-User-Id": "7"}).text == "You are tester"
    assert client.get("/me", headers={"X-User-Id": "1"}).status_code == 401
