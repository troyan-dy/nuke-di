from unittest.mock import AsyncMock, call

from nuke_di import Dependencies, Shutdown

from testing.clients import Database, Mailer, Queue
from testing.jobs.reminders import reminders
from testing.workers.signups import signups


async def test_job_directly() -> None:
    """A @job is a plain coroutine on import: call it with AsyncMocks and keyword parameters."""
    db, mailer = AsyncMock(), AsyncMock()
    db.unconfirmed.return_value = ["bob@example.com"]

    await reminders(db, mailer, limit=5)

    db.unconfirmed.assert_awaited_once_with(5)
    mailer.send.assert_awaited_once_with("bob@example.com", "Please confirm your email")


async def test_job_through_container(di: Dependencies) -> None:
    """di.inject(job): the clients wired as in production, mocks registered first."""
    db = di.mock(Database)
    db.unconfirmed.return_value = ["bob@example.com", "carol@example.com"]
    mailer = di.mock(Mailer)
    injected = di.inject(reminders)

    async with di:
        await injected(dry_run=True)

    assert db.unconfirmed.await_args_list == [call(100)]
    mailer.send.assert_not_awaited()


class LastMessageQueue(Queue):
    """
    A fake queue that hands out one message and then does what SIGTERM would do.
    """

    def __init__(self, shutdown: Shutdown) -> None:
        super().__init__()
        self._shutdown = shutdown

    async def get(self) -> str:
        self._shutdown.set()
        return "dave@example.com"


async def test_worker_stops_on_shutdown() -> None:
    """A @worker stops when a fake sets Shutdown, after finishing the current message."""
    shutdown = Shutdown()
    users = AsyncMock()
    users.register.return_value = 4

    await signups(LastMessageQueue(shutdown), users, shutdown)

    users.register.assert_awaited_once_with("dave@example.com")
