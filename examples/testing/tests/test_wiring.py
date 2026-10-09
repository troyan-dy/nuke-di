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
        self.db, self.table = db, table


async def audited(audit: Audit) -> None: ...


def test_wiring_catches_a_broken_init() -> None:
    """The same inject() fails on a broken __init__ with the error a real run would print."""
    with pytest.raises(InvalidSignatureError, match=r'Argument "table" of "Audit.__init__" is str'):
        Dependencies().inject(audited)


def test_graph() -> None:
    """deps.graph(): assert on which clients an entrypoint pulls in and what each one needs."""
    deps = Dependencies()
    deps.inject(signups)
    nodes = {node.name: node for node in deps.graph().nodes}

    assert list(nodes["Signups"].dependencies) == ["db", "mailer"]
    leaves = {name for name, node in nodes.items() if not node.dependencies}
    assert leaves == {"Queue", "Database", "Mailer", "Shutdown"}
    assert "Database --> Signups" in deps.graph().to_mermaid()


def test_graph_shows_replacements() -> None:
    """A mocked client shows up in the graph with `replacement` set: it is never connected."""
    deps = Dependencies()
    db = deps.mock(Database)
    deps.inject(reminders)
    nodes = {node.name: node for node in deps.graph().nodes}

    assert nodes["Database"].replacement is db
    assert nodes["Mailer"].replacement is None
