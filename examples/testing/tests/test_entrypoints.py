from unittest.mock import AsyncMock, call

from nuke_di import Dependencies

from testing.clients import Database, Mailer
from testing.jobs.reminders import reminders
from testing.workers.signups import signups


async def test_job_directly() -> None:
    """Import an entrypoint as a plain coroutine and call it with explicit arguments."""
    db, mailer = AsyncMock(), AsyncMock()
    db.unconfirmed.return_value = ["bob@example.com"]

    await reminders(db, mailer, limit=5)

    db.unconfirmed.assert_awaited_once_with(5)
    mailer.send.assert_awaited_once_with("bob@example.com", "Please confirm your email")


async def test_job_through_container(di: Dependencies) -> None:
    """Wire the clients as in production, with test replacements."""
    db = di.mock(Database)
    db.unconfirmed.return_value = ["bob@example.com", "carol@example.com"]
    mailer = di.mock(Mailer)
    injected = di.inject(reminders)

    async with di:
        await injected(dry_run=True)

    assert db.unconfirmed.await_args_list == [call(100)]
    mailer.send.assert_not_awaited()


async def test_signup_registers_the_queued_email() -> None:
    queue, users = AsyncMock(), AsyncMock()
    queue.get.return_value = "dave@example.com"
    users.register.return_value = 4

    await signups(queue, users)

    users.register.assert_awaited_once_with("dave@example.com")
