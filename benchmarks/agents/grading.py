"""
The grades of benchmarks/agents/evaluate.py that run a command: the hidden tests of a task and mypy with the plugin
of nuke-di, and the project and the transcript they grade.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Final

from harness import QUIET, agent, prepare, run
from tasks import HERE, REFERENCE, Session, Task

HIDDEN: Final = "hidden"
MYPY: Final = "mypy"
PYTEST: Final = ("pytest", QUIET, "-p", "no:cacheprovider")
GRADE_TIMEOUT_SECONDS: Final = 300
# The directories of a project that are not its code: git's, and the hidden tests, copied in for the grade
_SKIPPED: Final = frozenset((".git", HIDDEN))
_MYPY_INI: Final = "[mypy]\nplugins = nuke_di.mypy\nignore_missing_imports = True\n"
# The end of the output of a failed grade kept in the notes
_TAIL: Final = 1500
_TRANSCRIPT: Final = "transcript.jsonl"


def python_files(project: Path) -> list[Path]:
    found = project.rglob("*.py")
    return [path for path in found if _SKIPPED.isdisjoint(path.parts)]


def hidden_tests(task: Task, project: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """
    The tests of hidden/ that grade `task`, copied into the project after the agent finished, and their run.
    """
    hidden = project / HIDDEN
    shutil.rmtree(hidden, ignore_errors=True)
    hidden.mkdir()
    for name in task.hidden:
        shutil.copy(HERE / HIDDEN / name, hidden / name)
    tests = [f"{HIDDEN}/{test_file}" for test_file in task.hidden]
    # --noconftest: a fixture of the agent's conftest.py must not change what the hidden tests see
    return run([*PYTEST, "--noconftest", *tests], project, env, timeout=GRADE_TIMEOUT_SECONDS)


def mypy_check(project: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    config = project.parent / "mypy.ini"
    config.write_text(_MYPY_INI)
    files = [str(path) for path in python_files(project)]
    command = [MYPY, "--config-file", str(config), "--no-incremental", "--explicit-package-bases", *files]
    return run(command, project, env, timeout=GRADE_TIMEOUT_SECONDS)


def failing_notes(project: Path, *processes: tuple[str, subprocess.CompletedProcess[str]]) -> str:
    """
    The end of the output of every grade that failed, each under its name.
    """
    failing = []
    for name, process in processes:
        if process.returncode:
            tail = process.stdout[-_TAIL:]
            failing.append(f"{name}:\n{tail}")
    # Relative to the work directory, which is a temporary one of the machine that ran it
    return "\n".join(failing).replace(str(project.parent.parent), "<workdir>")


def transcript_of(task: Task, condition: str, project: Path, session: Session) -> str:
    """
    The transcript of the run of `task` under `condition` in `project`, after the agent made it there; the one of
    an earlier run when `session` grades again.
    """
    saved = project.parent / _TRANSCRIPT
    if session.regrade:
        # The project an agent left in an earlier run, graded again: no new session
        return saved.read_text()
    shutil.rmtree(project.parent, ignore_errors=True)
    prepare(task, condition, project)
    if session.model == REFERENCE:
        # A known-good solution, with no agent: reference/<task> over the project, if there is one
        solution = HERE / REFERENCE / task.name
        if solution.is_dir():
            shutil.copytree(solution, project, dirs_exist_ok=True)
        transcript = ""
    else:
        transcript = agent(task, condition, project, session.env, session.model)
    saved.write_text(transcript)
    return transcript
