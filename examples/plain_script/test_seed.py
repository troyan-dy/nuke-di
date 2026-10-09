from pathlib import Path

import pytest
from nuke_di import Dependencies

from plain_script.clients import Customers, Sqlite
from plain_script.seed import DEMO_CUSTOMERS, seed


async def test_seed_against_a_real_file(di: Dependencies, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.sqlite3"))  # read when Sqlite is resolved
    injected = di.inject(seed)
    db = di.resolve(Sqlite)
    async with di:
        assert await injected(rows=DEMO_CUSTOMERS) == 5
        assert await injected(rows=DEMO_CUSTOMERS[:2]) == 2  # a second run starts from scratch
        assert await db.fetchall("SELECT name FROM customers") == [("Ada Lovelace",), ("Alan Turing",)]


async def test_seed_with_a_mock(di: Dependencies) -> None:
    customers = di.mock(Customers)  # Sqlite is never resolved, so no file is opened
    customers.count.return_value = 1
    injected = di.inject(seed)
    async with di:
        assert await injected(rows=[("Ada", "ada@example.com")]) == 1

    customers.recreate.assert_awaited_once_with()
    customers.add_many.assert_awaited_once_with([("Ada", "ada@example.com")])
