"""
The public API under `mypy --strict`, as a user's test file sees it: the README "Testing" examples
have to pass without a `type: ignore`.
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mypy")

# What a test touches: the mock API of an autospec Replacement, the type of a fake, of a resolved
# dataclass client and of an injected function
SNIPPET = """
from collections.abc import Callable, Coroutine
from typing import Any, assert_type

from nuke_di import Client, Dependencies, client_dataclass


class Database(Client):
    async def fetch_user(self, user_id: int) -> str:
        return f"user-{user_id}"


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


@client_dataclass(frozen=True)
class UserService(Client):
    db: Database

    async def greet(self, user_id: int) -> str:
        return f"Hello, {await self.db.fetch_user(user_id)}!"


async def handler(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def test_mock(deps: Dependencies) -> None:
    db = deps.mock(Database)  # an autospec mock, Any like unittest.mock.create_autospec()
    db.fetch_user.return_value = "alice"
    users = deps.resolve(UserService)
    assert_type(users, UserService)
    injected = deps.inject(handler)
    assert_type(injected, Callable[..., Coroutine[Any, Any, str]])
    assert_type(await injected(1), str)
    db.fetch_user.assert_awaited_once_with(1)
    assert_type(deps.mock(Database, FakeDatabase()), Database)


def test_override(deps: Dependencies) -> None:
    with deps.override(Database) as db:
        db.fetch_user.return_value = "bob"
    with deps.override(Database, FakeDatabase()) as fake:
        assert_type(fake, Database)
"""


def test_public_api_passes_strict_mypy(tmp_path: Path) -> None:
    source = tmp_path / "test_snippet.py"
    source.write_text(SNIPPET)
    command = [sys.executable, "-m", "mypy", "--strict", "--cache-dir", str(tmp_path / "cache"), str(source)]

    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603 - fixed command

    assert result.returncode == 0, result.stdout
