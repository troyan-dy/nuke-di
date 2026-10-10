from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, call

import pytest
from nuke_di import DI

from taskiq_app.clients import Database, Mailer, Reports
from taskiq_app.tasks import broker, send_report, send_reports


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        if user_id == 7:
            return "tester"
        raise LookupError(f"user {user_id} not found")


@pytest.fixture
async def mailer() -> AsyncIterator[Any]:
    # Replaced before the broker starts: the worker's startup resolves the clients; a Replacement is never connected
    with DI.override(Database, FakeDatabase()), DI.override(Mailer) as mailer:
        await broker.startup()
        try:
            yield mailer
        finally:
            await broker.shutdown()


async def test_send_report(mailer: Any) -> None:
    task = await send_report.kiq(7)
    result = await task.wait_result()

    assert result.return_value == "tester"
    mailer.send.assert_awaited_once_with("tester", "your weekly report")


async def test_unknown_user_fails_the_task(mailer: Any) -> None:
    task = await send_report.kiq(1)
    result = await task.wait_result()

    assert isinstance(result.error, LookupError)
    mailer.send.assert_not_awaited()


async def test_admin_is_notified_after_the_reports(mailer: Any) -> None:
    task = await send_reports.kiq([7, 7])
    await task.wait_result()

    assert mailer.send.await_args_list == [
        call("tester", "your weekly report"),
        call("tester", "your weekly report"),
        call("admin", "taskiq_app.tasks:send_reports finished"),
    ]


async def test_task_without_the_worker_is_not_connected() -> None:
    # No broker.startup(): an InMemoryBroker runs the task anyway, and the client is missing
    task = await send_report.kiq(7)
    result = await task.wait_result()

    assert isinstance(result.error, RuntimeError)
    assert "Reports is not connected" in str(result.error)


async def test_task_called_directly() -> None:
    # The function stays a function: clients are passed by hand, without a broker
    reports = AsyncMock(spec=Reports)
    reports.send_weekly.return_value = "tester"

    assert await send_report(7, reports) == "tester"
