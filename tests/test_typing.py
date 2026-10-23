"""
The public API under `mypy --strict`, as a user's test file sees it: the examples of the "Testing" guide
page have to pass without a `type: ignore`, so they are checked straight from docs/guide/testing.md.
"""

import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Final

import pytest

from tests.test_docs import document, split

pytest.importorskip("mypy")

# The blocks that the "Testing" blocks build on, by content, at the file each one names in its first line:
# the Quick start of README.md, which has no name, and the modules of the "Workers and jobs" page
QUICK_START = "quick_start.py"
MODULES: Final = (
    "class UserService(Client):",  # Quick start: Database, UserService, handler
    "class Warehouse(Client):",  # app/clients.py: Postgres, Warehouse, Queue
    "Option(help=",  # app/jobs/sync.py with its Parameters: sync(pg, warehouse, day=..., tables=...)
    "async def consumer(queue: Queue, shutdown: Shutdown)",  # app/workers/consumer.py
)
# Every "Testing" block says in prose what it takes from the Quick start or the jobs; a fragment imports nothing
QUICK_START_HEADER = """
from nuke_di import DI, Dependencies
from quick_start import Database, UserService, handler
"""
JOBS_HEADER = """
import datetime
from unittest.mock import AsyncMock, call

from nuke_di import Dependencies, Shutdown
from app.clients import Postgres, Warehouse
from app.jobs.sync import sync
from app.workers.consumer import consumer
"""

# What a test touches: the mock API of an autospec Replacement, the type of a Replacement of your own,
# of a resolved dataclass client and of an injected function
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
    assert_type(deps.mock(Database, None), Any)


def test_override(deps: Dependencies) -> None:
    with deps.override(Database) as db:
        db.fetch_user.return_value = "bob"
    with deps.override(Database, new=None) as same:
        same.fetch_user.return_value = "bob"
    with deps.override(Database, FakeDatabase()) as fake:
        assert_type(fake, Database)
"""


def python_blocks(text: str) -> list[str]:
    """
    The Python code blocks of a Markdown document, without the fences.
    """
    blocks = []
    for block in split(text)[1]:
        lines = textwrap.dedent(block).splitlines()
        if lines[0] == "```python":
            code = "\n".join(lines[1:-1])
            blocks.append(f"{code}\n")
    return blocks


def strict_mypy(root: Path) -> subprocess.CompletedProcess[str]:
    # No config file in `root`: the flags are the whole configuration
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--strict",
        "--cache-dir",
        str(root / ".mypy_cache"),
        ".",
    ]
    return subprocess.run(command, capture_output=True, text=True, check=False, cwd=root)  # noqa: S603 - fixed command


def lay_out(root: Path, block: str) -> None:
    """
    A block the "Testing" blocks build on, at the file its first line names; the Quick start at quick_start.py.
    """
    first = block.splitlines()[0]
    name = first.removeprefix("# ") if first.startswith("# app/") else QUICK_START
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(block)


def lay_out_modules(root: Path) -> None:
    """
    The blocks that MODULES names, laid out under `root` with an `__init__.py` in every package.
    """
    # The guide page first: README.md has blocks with the same markers, in a single-file sync.py, which would
    # otherwise be found first and laid out at the wrong path
    blocks = python_blocks(document(page="workers-and-jobs")) + python_blocks(document())
    for marker in MODULES:
        lay_out(root, next(block for block in blocks if marker in block))
    for package in root.glob("app/**/"):
        (package / "__init__.py").touch()


def header(block: str) -> str:
    """
    The imports a "Testing" block needs on top of its own.
    """
    if "from app." in block:
        return ""  # a whole test module, with its own imports from the app laid out above
    if "Database" in block:
        return QUICK_START_HEADER
    if re.search(r"\b(sync|consumer)\b", block):
        return JOBS_HEADER
    return ""


def test_testing_guide_examples_pass_strict_mypy(tmp_path: Path) -> None:
    lay_out_modules(tmp_path)
    examples = python_blocks(document(page="testing"))
    assert examples
    for number, block in enumerate(examples, start=1):
        (tmp_path / f"testing_{number}.py").write_text(header(block) + block)

    checked = strict_mypy(tmp_path)

    assert checked.returncode == 0, checked.stdout


def test_public_api_passes_strict_mypy(tmp_path: Path) -> None:
    (tmp_path / "test_snippet.py").write_text(SNIPPET)

    checked = strict_mypy(tmp_path)

    assert checked.returncode == 0, checked.stdout
