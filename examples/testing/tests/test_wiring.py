from collections.abc import Callable

import pytest
from nuke_di import Client, Dependencies, InvalidSignatureError

from testing.clients import Database
from testing.jobs.reminders import reminders
from testing.main import register
from testing.workers.signups import signups


@pytest.mark.parametrize("entrypoint", [register, reminders, signups])
def test_entrypoint_resolves(entrypoint: Callable[..., object]) -> None:
    """Every entrypoint resolves: runs every __init__ of its tree, connects nothing."""
    Dependencies().inject(entrypoint)


class Audit(Client):
    def __init__(self, db: Database, table: str) -> None:  # `table` is not a client and has no default
        self.db = db
        self.table = table


async def audited(audit: Audit) -> None: ...


def test_wiring_catches_a_broken_init() -> None:
    """The same inject() fails on a broken __init__ with the error a real run would print."""
    with pytest.raises(InvalidSignatureError, match=r'Argument "table" of "Audit.__init__" is str'):
        Dependencies().inject(audited)  # type: ignore[nuke-di]
