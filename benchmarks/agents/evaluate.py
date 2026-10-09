"""
Does the Agent Skill help a coding agent write nuke-di code? Each task runs in a fresh project with Claude Code in
print mode, once per condition, then the project is graded by commands that need no judgment.

    uv run python benchmarks/agents/evaluate.py --model sonnet --repeats 2 --jobs 4

Conditions:

    baseline   nuke-di installed in the environment, nothing else
    skill      the same, plus this repository as a Claude Code plugin (--plugin-dir), i.e. skills/nuke-di/SKILL.md
    agents-md  the same as baseline, plus the block of docs/guide/agents.md as the project's CLAUDE.md

Grades, all of which must pass:

    agent tests  `pytest` over the project, the tests the agent wrote included
    hidden       the task's own tests from hidden/, copied in after the agent has finished
    mypy         mypy with the nuke_di.mypy plugin reports no [nuke-di] error
    shapes       none of the designs nuke-di rejects: see `shapes()`

`--model reference` grades the solutions of reference/ instead, with no agent: every task must pass, or a grade
is wrong. Not part of the test suite: every run costs an agent session. The results go to
results/<date>-<model>.json, and the summary table is printed for benchmarks/agents/README.md.
"""

import argparse
import ast
import concurrent.futures
import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONDITIONS = ["baseline", "skill", "agents-md"]
NEW_PROJECT = (
    "This is a new, empty project. nuke-di (the `nuke_di` package), FastAPI, httpx, pytest, pytest-asyncio and mypy "
    "are installed in the active Python environment; `python`, `pytest` and `mypy` are on PATH. pytest.ini sets "
    "`asyncio_mode = auto` and puts the project root on the import path."
)
EXISTING_PROJECT = (
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


TASKS = [
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
        ("test_fastapi_app.py",),
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
]


@dataclass
class Result:
    task: str
    condition: str
    repeat: int
    passed: bool = False
    grades: dict[str, bool] = field(default_factory=dict)
    shapes: list[str] = field(default_factory=list)
    skill_used: bool = False
    seconds: float = 0.0
    cost_usd: float | None = None
    notes: str = ""


def run(command: list[str], cwd: Path, env: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    # Fixed commands of this script: claude, pytest, mypy, git, uv
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603


def environment(workdir: Path) -> dict[str, str]:
    """A virtual environment with nuke-di built from this checkout and what the tasks need, shared by every run."""
    venv = workdir / "venv"
    if not venv.exists():
        wheels = workdir / "wheels"
        env = dict(os.environ)
        subprocess.run(["uv", "build", "--wheel", "-q", "-o", str(wheels), str(ROOT)], check=True, env=env)  # noqa: S603, S607
        wheel = next(wheels.glob("nuke_di-*.whl"))
        subprocess.run(["uv", "venv", "-q", "--python", "3.12", str(venv)], check=True)  # noqa: S603, S607
        packages = [f"{wheel}[fastapi]", "httpx", "pytest", "pytest-asyncio", "mypy"]
        subprocess.run(["uv", "pip", "install", "-q", "--python", str(venv / "bin" / "python"), *packages], check=True)  # noqa: S603, S607
    env = {key: value for key, value in os.environ.items() if not key.startswith(("VIRTUAL_ENV", "UV_", "PYTHON"))}
    env["VIRTUAL_ENV"] = str(venv)
    env["PATH"] = f"{venv / 'bin'}{os.pathsep}{env['PATH']}"
    return env


def agents_md_block() -> str:
    """The block of docs/guide/agents.md that a project pastes into its AGENTS.md / CLAUDE.md."""
    text = (ROOT / "docs" / "guide" / "agents.md").read_text(encoding="utf-8")
    match = re.search(r"```markdown\n(.*?)```", text, flags=re.DOTALL)
    assert match is not None
    return match.group(1)


def prepare(task: Task, condition: str, project: Path) -> None:
    project.mkdir(parents=True)
    pytest_ini = "[pytest]\nasyncio_mode = auto\nasyncio_default_fixture_loop_scope = function\npythonpath = .\n"
    (project / "pytest.ini").write_text(pytest_ini)
    if task.example is not None:
        shutil.copytree(
            ROOT / "examples" / task.example,
            project / task.example,
            ignore=shutil.ignore_patterns("README.md", "__pycache__"),
        )
    if condition == "agents-md":
        (project / "CLAUDE.md").write_text(agents_md_block())
    git = ["git", "-c", "user.name=eval", "-c", "user.email=eval@example.com"]
    for command in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        [*git, "commit", "-q", "-m", "start", "--allow-empty"],
    ):
        subprocess.run(command, cwd=project, check=True, capture_output=True)  # noqa: S603


def agent(task: Task, condition: str, project: Path, env: dict[str, str], model: str) -> tuple[dict[str, object], str]:
    command = [
        "claude",
        "-p",
        task.prompt,
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        # No user settings, plugins, hooks or MCP servers: only what the condition gives
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Bash Read Edit Write Glob Grep Skill TodoWrite",
        "--disallowedTools",
        "WebFetch WebSearch",
    ]
    if condition == "skill":
        command += ["--plugin-dir", str(ROOT)]
    done = run(command, project, env, timeout=1200)
    events = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
    final: dict[str, object] = next((event for event in reversed(events) if event.get("type") == "result"), {})
    return final, done.stdout


def python_files(project: Path) -> list[Path]:
    return [path for path in project.rglob("*.py") if ".git" not in path.parts and "hidden" not in path.parts]


def retries(connect: ast.AsyncFunctionDef) -> list[tuple[int, str]]:
    """
    A retry in a connect(): a `while` loop, a loop that sleeps or catches an exception, a counter of attempts.
    A plain `for` over data, e.g. to warm a cache, is not one.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(connect):
        if isinstance(node, ast.While):
            found.append((node.lineno, "a while loop in connect()"))
        elif isinstance(node, ast.For | ast.AsyncFor):
            inner = list(ast.walk(node))
            if any(isinstance(each, ast.Try) for each in inner):
                found.append((node.lineno, "a loop that catches exceptions in connect()"))
            elif any(isinstance(each, ast.Call) and "sleep" in ast.unparse(each.func) for each in inner):
                found.append((node.lineno, "a loop that sleeps in connect()"))
            elif re.search(r"attempt|retr|tries|backoff", ast.unparse(node.target), flags=re.IGNORECASE):
                found.append((node.lineno, "a loop over attempts in connect()"))
    return found


def shapes(project: Path) -> list[str]:
    """
    The designs nuke-di rejects, found in the code of the project (tests included): one line per finding.
    """
    found: list[str] = []
    for path in python_files(project):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            found.append(f"{path.name}: does not parse")
            continue
        name = path.relative_to(project)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name | ast.Attribute):
                ident = node.id if isinstance(node, ast.Name) else node.attr
                if ident == "NotSingletonClient":
                    found.append(f"{name}:{node.lineno}: NotSingletonClient (ADR-0006)")
                if ident in {"bind", "provide", "register", "provider"} and isinstance(node, ast.Attribute):
                    found.append(f"{name}:{node.lineno}: .{ident} (ADR-0005 / ADR-0008)")
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "connect":
                found += [f"{name}:{line}: {what} (ADR-0009)" for line, what in retries(node)]
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and re.search(
                        r"AsyncClient|ClientSession|create_pool", ast.unparse(inner.func)
                    ):
                        found.append(f"{name}:{inner.lineno}: I/O object created in __init__")
        if re.search(r"^\s*(import|from) (tenacity|backoff)\b", source, flags=re.MULTILINE):
            found.append(f"{name}: a retry library (ADR-0009)")
    return sorted(set(found))


def grade(task: Task, project: Path, env: dict[str, str], result: Result) -> None:
    tests = run(["pytest", "-q", "-p", "no:cacheprovider"], project, env, timeout=300)
    result.grades["agent tests"] = tests.returncode == 0
    hidden = project / "hidden"
    hidden.mkdir(exist_ok=True)
    for name in task.hidden:
        shutil.copy(HERE / "hidden" / name, hidden / name)
    graded = run(
        ["pytest", "-q", "-p", "no:cacheprovider", "--rootdir", ".", *(f"hidden/{name}" for name in task.hidden)],
        project,
        env,
        timeout=300,
    )
    result.grades["hidden"] = graded.returncode == 0
    config = project.parent / "mypy.ini"
    config.write_text("[mypy]\nplugins = nuke_di.mypy\nignore_missing_imports = True\n")
    files = [str(path) for path in python_files(project)]
    checked = run(["mypy", "--config-file", str(config), "--no-incremental", *files], project, env, timeout=300)
    result.grades["mypy"] = "[nuke-di]" not in checked.stdout
    result.shapes = shapes(project)
    result.grades["shapes"] = not result.shapes
    result.passed = all(result.grades.values())
    failing = [
        f"{key}:\n{value.stdout[-1500:]}"
        for key, value in (("tests", tests), ("hidden", graded), ("mypy", checked))
        if value.returncode
    ]
    result.notes = "\n".join(failing)


def one(task: Task, condition: str, repeat: int, workdir: Path, env: dict[str, str], model: str) -> Result:
    result = Result(task.name, condition, repeat)
    project = workdir / f"{task.name}-{condition}-{repeat}" / "project"
    shutil.rmtree(project.parent, ignore_errors=True)
    prepare(task, condition, project)
    started = time.monotonic()
    final: dict[str, object]
    if model == "reference":
        # The grading of a known-good solution, with no agent: reference/<task> over the project, if there is one
        if (HERE / "reference" / task.name).is_dir():
            shutil.copytree(HERE / "reference" / task.name, project, dirs_exist_ok=True)
        final, transcript = {}, ""
    else:
        final, transcript = agent(task, condition, project, env, model)
    result.seconds = round(time.monotonic() - started, 1)
    (project.parent / "transcript.jsonl").write_text(transcript)
    cost = final.get("total_cost_usd")
    result.cost_usd = float(cost) if isinstance(cost, int | float) else None
    result.skill_used = '"name":"Skill"' in transcript.replace(" ", "") and "nuke-di" in transcript
    grade(task, project, env, result)
    print(
        f"{task.name:<14} {condition:<10} #{repeat}  {'PASS' if result.passed else 'fail'}  {result.grades}", flush=True
    )
    return result


def table(results: list[Result], conditions: list[str]) -> str:
    rows = ["| Task | " + " | ".join(conditions) + " |", "|---|" + "---:|" * len(conditions)]
    for task in dict.fromkeys(result.task for result in results):
        cells = []
        for condition in conditions:
            runs = [result for result in results if result.task == task and result.condition == condition]
            cells.append(f"{sum(result.passed for result in runs)}/{len(runs)}" if runs else "—")
        rows.append(f"| {task} | " + " | ".join(cells) + " |")
    totals = []
    for condition in conditions:
        runs = [result for result in results if result.condition == condition]
        totals.append(f"**{sum(result.passed for result in runs)}/{len(runs)}**" if runs else "—")
    rows.append("| **all** | " + " | ".join(totals) + " |")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent evals of nuke-di")
    parser.add_argument("--model", default="sonnet", help="a Claude model, or `reference` to grade reference/")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--conditions", nargs="+", default=["baseline", "skill"], choices=CONDITIONS)
    parser.add_argument(
        "--tasks", nargs="+", default=[task.name for task in TASKS], choices=[task.name for task in TASKS]
    )
    parser.add_argument("--workdir", type=Path, default=Path(tempfile.gettempdir()) / "nuke-di-agent-evals")
    args = parser.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    env = environment(args.workdir)
    tasks = [task for task in TASKS if task.name in args.tasks]
    runs = [
        (task, condition, repeat)
        for repeat in range(1, args.repeats + 1)
        for task in tasks
        for condition in args.conditions
    ]
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(one, *run_, args.workdir, env, args.model) for run_ in runs]
        results = [future.result() for future in futures]

    stamp = datetime.date.today().isoformat()
    output = HERE / "results" / f"{stamp}-{args.model}.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps([asdict(result) for result in results], indent=2) + "\n")
    print(f"\n{output.relative_to(ROOT)}\n")
    print(table(results, args.conditions))


if __name__ == "__main__":
    main()
