"""
What benchmarks/agents/evaluate.py asks of an agent, and what it records of an attempt: the tasks, the conditions,
the outcome of one run and the summary table of a series.
"""

import datetime
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final

HERE: Final = Path(__file__).resolve().parent
ROOT: Final = HERE.parent.parent
CONDITIONS: Final = ("baseline", "skill", "agents-md")
# The model that grades the solutions of reference/, with no agent
REFERENCE: Final = "reference"
NEW_PROJECT: Final = (
    "This is a new, empty project. nuke-di (the `nuke_di` package), FastAPI, httpx, pytest, pytest-asyncio and mypy "
    "are installed in the active Python environment; `python`, `pytest` and `mypy` are on PATH. pytest.ini sets "
    "`asyncio_mode = auto` and puts the project root on the import path."
)
EXISTING_PROJECT: Final = (
    "This project uses nuke-di (the `nuke_di` package); FastAPI, httpx, pytest, pytest-asyncio and mypy are installed "
    "in the active Python environment, and `python`, `pytest` and `mypy` are on PATH. Run the tests with `pytest`."
)


@dataclass(frozen=True)
class Task:
    name: str
    prompt: str
    # The example of examples/ the project starts from; None for an empty project
    example: str | None
    # The test files of hidden/ that grade it
    hidden: tuple[str, ...]


TASKS: Final = (
    Task(
        "fresh_fastapi",
        f"""{NEW_PROJECT}

Write a FastAPI application in `app/main.py` (the variable `app`) that uses nuke-di for dependency injection:

- `GET /orders/{{order_id}}` returns `{{"order_id": ..., "item": ..., "paid": ...}}`, 404 for an unknown order.
- The orders come from an `OrderStore`: an in-memory dict standing in for a Postgres pool, which loads
  `{{1: "book", 2: "pen"}}` when the application starts.
- `paid` comes from a payments service over HTTP: `GET {{PAYMENTS_URL}}/payments/{{order_id}}` answers
  `{{"paid": true}}`. Use one shared `httpx.AsyncClient`; `PAYMENTS_URL` comes from the environment.

Add tests under `tests/` that pass without network access.""",
        None,
        ("test_fresh_fastapi.py",),
    ),
    Task(
        "fresh_job",
        f"""{NEW_PROJECT}

Write a nuke-di job in `sync_job.py`, the function `sync`, run as `python sync_job.py --day 2026-10-01`. It copies
the rows of that day from `Postgres` to `Redis`. Both are stand-ins for now: `Postgres` prints `postgres: connected`
when it connects and returns three fake rows for any day; `Redis` prints `redis: connected` and keeps the rows in a
dict. When Redis connects it warms itself up from Postgres, so Redis must connect only after Postgres has connected.
Add tests.""",
        None,
        ("test_fresh_job.py",),
    ),
    Task(
        "fresh_worker",
        f"""{NEW_PROJECT}

Write `consumer.py`: a long-running process started with `python consumer.py`, built with nuke-di, whose main
function is `consume`. It takes messages from a `Queue` client (an in-memory stand-in with
`async def get(self) -> str` that produces a new message every 0.2 seconds) and prints `processed <message>` for
each. On SIGTERM or Ctrl+C it must finish the message it is processing and exit cleanly. Add tests.""",
        None,
        ("test_fresh_worker.py",),
    ),
    Task(
        "protocol",
        f"""{EXISTING_PROJECT}

In `fastapi_app`, make `UserCache` depend on an abstract `UserSource` protocol (with
`async def fetch_users(self) -> dict[int, str]`) instead of the concrete `Database`, so that other user sources can
be plugged in later and tests can use an in-memory source. Keep the tests passing.""",
        "fastapi_app",
        ("test_fastapi_app.py",),
    ),
    Task(
        "startup",
        f"""{EXISTING_PROJECT}

In production the `Database` of `fastapi_app` is sometimes unreachable for the first few seconds after the pod
starts, and the application fails on startup. Make the startup handle that. Keep the tests passing.""",
        "fastapi_app",
        ("test_fastapi_app.py", "test_startup.py"),
    ),
    Task(
        "audit",
        f"""{EXISTING_PROJECT}

Add request auditing to `fastapi_app`: every `GET /users/{{user_id}}` gets a request id of its own (a new uuid4 per
request) and is recorded in an audit log as `{{"request_id": ..., "user_id": ...}}`. Add `GET /audit`, which returns
the list of records in the order of the requests. Keep the tests passing and add tests for the audit.""",
        "fastapi_app",
        ("test_fastapi_app.py", "test_audit.py"),
    ),
)


@dataclass
class Attempt:
    """
    One run of a task under a condition: its grades, the rejected designs found and what the session cost.
    """

    task: str
    condition: str
    repeat: int
    passed: bool = False
    grades: dict[str, bool] = field(default_factory=dict)
    shapes: list[str] = field(default_factory=list)
    skill_used: bool = False
    seconds: float = float(0)
    cost_usd: float | None = None
    turns: int | None = None
    notes: str = ""


@dataclass(frozen=True)
class Session:
    """
    What every run of a series shares: the work directory, the environment, the model, and whether the projects
    of an earlier run are graded again.
    """

    workdir: Path
    env: dict[str, str]
    model: str
    regrade: bool


def agents_md_block() -> str:
    """
    The block of docs/guide/agents.md that a project pastes into its AGENTS.md / CLAUDE.md.
    """
    text = (ROOT / "docs" / "guide" / "agents.md").read_text(encoding="utf-8")
    match = re.search(r"```markdown\n(.*?)```", text, flags=re.DOTALL)
    assert match is not None
    return match.group(1)


def save(attempts: Sequence[Attempt], model: str) -> Path:
    """
    The attempts into results/<date>-<model>.json; the file, relative to the repository.
    """
    stamp = datetime.date.today().isoformat()
    output = HERE / "results" / f"{stamp}-{model}.json"
    output.parent.mkdir(exist_ok=True)
    text = json.dumps([asdict(attempt) for attempt in attempts], indent=2)
    output.write_text(f"{text}\n")
    return output.relative_to(ROOT)


def table(attempts: Sequence[Attempt], conditions: Sequence[str]) -> str:
    """
    The passes of every task under every condition, then of all of them, for benchmarks/agents/README.md.
    """
    rule = "---:|" * len(conditions)
    rows = ["| Task | {} |".format(" | ".join(conditions)), f"|---|{rule}"]
    for task in dict.fromkeys(attempt.task for attempt in attempts):
        of_task = [attempt for attempt in attempts if attempt.task == task]
        rows.append(_row(task, of_task, conditions))
    rows.append(_row("**all**", attempts, conditions, bold=True))
    return "\n".join(rows)


def _row(first: str, attempts: Sequence[Attempt], conditions: Sequence[str], *, bold: bool = False) -> str:
    cells = [first]
    for condition in conditions:
        runs = [attempt for attempt in attempts if attempt.condition == condition]
        passed = sum(attempt.passed for attempt in runs)
        count = f"{passed}/{len(runs)}"
        if runs and bold:
            cells.append(f"**{count}**")
        elif runs:
            cells.append(count)
        else:
            cells.append("—")
    return "| {} |".format(" | ".join(cells))
