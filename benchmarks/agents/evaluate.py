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

The tasks and the record of an attempt are tasks.py, the agent's session harness.py, its transcript
transcripts.py, the grades that run a command grading.py.
"""

import argparse
import ast
import sys
import tempfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from detectors import PROVIDED, client_classes, findings
from grading import (
    GRADE_TIMEOUT_SECONDS,
    MYPY,
    PYTEST,
    failing_notes,
    hidden_tests,
    mypy_check,
    python_files,
    transcript_of,
)
from harness import environment, run
from tasks import CONDITIONS, REFERENCE, TASKS, Attempt, Session, Task, save, table
from transcripts import record, summary

# A line on stdout as a run finishes
_PROGRESS = "{task:<14} {condition:<10} #{repeat}  {verdict}  {grades}\n"


def shapes(project: Path) -> list[str]:
    """
    The designs nuke-di rejects, found in the code of the project (tests included): one line per finding. The
    detectors, one per design, are detectors.py and retries.py.
    """
    found: list[str] = []
    trees: dict[Path, ast.Module] = {}
    for path in python_files(project):
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            found.append(f"{path.name}: does not parse")
    provided = PROVIDED | client_classes(trees.values())
    for parsed in trees:
        found.extend(findings(project, parsed, trees[parsed], provided))
    return sorted(set(found))


def grade(task: Task, project: Path, env: dict[str, str], attempt: Attempt) -> None:
    tests = run(PYTEST, project, env, timeout=GRADE_TIMEOUT_SECONDS)
    graded = hidden_tests(task, project, env)
    checked = mypy_check(project, env)
    attempt.shapes = shapes(project)
    attempt.grades.update(
        {
            "agent tests": tests.returncode == 0,
            "hidden": graded.returncode == 0,
            # 1 is a type error of the agent's own, which is not graded; 2 means mypy could not check the project at all
            "mypy": checked.returncode in {0, 1} and "[nuke-di]" not in checked.stdout,
            "shapes": not attempt.shapes,
        }
    )
    attempt.passed = all(attempt.grades.values())
    attempt.notes += failing_notes(
        project,
        ("tests", tests),
        ("hidden", graded),
        (MYPY, checked),
    )


def one(task: Task, condition: str, repeat: int, session: Session) -> Attempt:
    attempt = Attempt(task.name, condition, repeat)
    project = session.workdir / f"{task.name}-{condition}-{repeat}" / "project"
    final, loaded = summary(transcript_of(task, condition, project, session))
    attempt.skill_used = loaded
    record(attempt, final)
    if not final and session.model != REFERENCE:
        attempt.notes = "the agent did not finish: timeout or crash\n"
    grade(task, project, session.env, attempt)
    sys.stdout.write(
        _PROGRESS.format(
            task=task.name,
            condition=condition,
            repeat=repeat,
            verdict="PASS" if attempt.passed else "fail",
            grades=attempt.grades,
        ),
    )
    sys.stdout.flush()
    return attempt


def main() -> None:
    args = _arguments()
    args.workdir.mkdir(parents=True, exist_ok=True)
    session = Session(args.workdir, environment(args.workdir), args.model, args.regrade)
    attempts = _attempts(args, session)
    output = save(attempts, args.model)
    sys.stdout.write(f"\n{output}\n\n")
    sys.stdout.write(f"{table(attempts, args.conditions)}\n")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the agent evals of nuke-di")
    parser.add_argument("--model", default="sonnet", help="a Claude model, or `reference` to grade reference/")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--conditions", nargs="+", default=["baseline", "skill"], choices=CONDITIONS)
    names = [task.name for task in TASKS]
    parser.add_argument("--tasks", nargs="+", default=names, choices=names)
    workdir = Path(tempfile.gettempdir()) / "nuke-di-agent-evals"
    parser.add_argument("--workdir", type=Path, default=workdir)
    parser.add_argument(
        "--regrade", action="store_true", help="grade the projects of an earlier run in --workdir again"
    )
    return parser.parse_args()


def _attempts(args: argparse.Namespace, session: Session) -> list[Attempt]:
    runs = list(_runs(args))
    with ThreadPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(one, *spec, session) for spec in runs]
        return [future.result() for future in futures]


def _runs(args: argparse.Namespace) -> Iterator[tuple[Task, str, int]]:
    """
    Every run of the series: the task, the condition and the repeat, every task under every condition first.
    """
    tasks = [chosen for chosen in TASKS if chosen.name in args.tasks]
    for repeat in range(1, args.repeats + 1):
        for task in tasks:
            for condition in args.conditions:
                yield task, condition, repeat


if __name__ == "__main__":
    main()
