from unittest.mock import call

import pytest
from nuke_di import DI, Dependencies

from testing.clients import Database, Mailer, Signups
from testing.main import main, register


class RecordingMailer(Mailer):
    """
    A hand-written fake: the real class with the I/O replaced by a list.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, to: str, subject: str) -> None:
        self.sent.append((to, subject))


async def test_mock_autospec(di: Dependencies) -> None:
    """di.mock(): an autospec mock with a return value, the calls checked afterwards."""
    db = di.mock(Database)
    db.add_user.return_value = 7
    mailer = di.mock(Mailer)
    injected = di.inject(register)

    async with di:
        assert await injected("dave@example.com") == 7

    assert db.add_user.await_args_list == [call("dave@example.com")]
    assert mailer.send.await_args_list == [call("dave@example.com", "Welcome")]


async def test_mock_follows_the_real_signatures(di: Dependencies) -> None:
    """An autospec mock rejects a method the client does not have and a call that does not match."""
    db = di.mock(Database)

    with pytest.raises(AttributeError):
        db.add_users  # noqa: B018
    with pytest.raises(TypeError):
        await db.add_user("dave@example.com", confirmed=True)


async def test_override_with_fake() -> None:
    """DI.override(cls, Fake()): a hand-written fake for one block; the other clients stay real."""
    mailer = RecordingMailer()
    with DI.override(Mailer, mailer):
        injected = DI.inject(register)
        async with DI:
            assert await injected("dave@example.com") == 4

    assert mailer.sent == [("dave@example.com", "Welcome")]
    assert not DI.clients  # flushed on exit: nothing leaks into the next test


async def test_override_autospec_across_two_runs(di: Dependencies) -> None:
    """override(cls) without `new`: an autospec mock that survives several `async with` cycles."""
    with di.override(Mailer) as mailer:
        for email in ["dave@example.com", "erin@example.com"]:
            users = di.resolve(Signups)
            async with di:  # disconnect() flushes the container, the override stays
                await users.register(email)

    assert mailer.send.await_args_list == [
        call("dave@example.com", "Welcome"),
        call("erin@example.com", "Welcome"),
    ]


async def test_code_that_calls_global_di(global_di: Dependencies) -> None:
    """global_di: test code that calls DI.inject() itself; DI is flushed before and after the test."""
    global_di.mock(Database).add_user.return_value = 42
    mailer = global_di.mock(Mailer)

    assert await main("dave@example.com") == 42

    mailer.send.assert_awaited_once_with("dave@example.com", "Welcome")
