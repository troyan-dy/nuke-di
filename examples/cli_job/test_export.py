import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from nuke_di import Dependencies

from cli_job.clients import Order, Orders
from cli_job.export import Format, export

ADA = Order(id=1, customer="ada", amount=42.0, created=datetime.date(2026, 10, 1))


async def test_export_called_directly(tmp_path: Path) -> None:
    # Importing the module does not run the job: it is a coroutine function with parameters as keywords
    orders = AsyncMock(spec=Orders)
    orders.since.return_value = [ADA]
    report = tmp_path / "report.csv"

    await export(orders, since=datetime.date(2026, 10, 1), limit=5, output=report)

    orders.since.assert_awaited_once_with(datetime.date(2026, 10, 1), 5)
    assert report.read_text() == "id,customer,amount,created\n1,ada,42.0,2026-10-01\n"


async def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    orders = AsyncMock(spec=Orders)
    orders.since.return_value = [ADA]

    report = tmp_path / "report.csv"

    await export(orders, since=datetime.date(2026, 10, 1), output=report, dry_run=True)

    assert not report.exists()


async def test_export_through_the_container(
    di: Dependencies, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The real Orders and Sqlite, as `python -m cli_job.export` wires them, on a database of the test
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.sqlite3"))
    injected = di.inject(export)
    async with di:
        await injected(since=datetime.date(2026, 10, 4), format=Format.JSON)

    exported = json.loads(capsys.readouterr().out)
    assert [(order["customer"], order["created"]) for order in exported] == [
        ("grace", "2026-10-05"),
        ("alan", "2026-10-04"),
    ]
