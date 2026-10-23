"""
How benchmarks/agents/evaluate.py runs an agent: the shared environment of the runs, a fresh project per run and
the Claude Code session.
"""

import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from tasks import ROOT, Task, agents_md_block

# The Python of the shared environment of the runs
PYTHON: Final = "3.12"
QUIET: Final = "-q"
_OWN_SETTINGS: Final = ("VIRTUAL_ENV", "UV_", "PYTHON")
_PACKAGES: Final = ("httpx", "pytest", "pytest-asyncio", "mypy")
# An agent session that takes longer than this is stopped and graded as it is
_AGENT_TIMEOUT_SECONDS: Final = 1200
_PYTEST_INI: Final = "[pytest]\nasyncio_mode = auto\nasyncio_default_fixture_loop_scope = function\npythonpath = .\n"
_GIT: Final = ("git", "-c", "user.name=eval", "-c", "user.email=eval@example.com")
_START: Final = (
    ("git", "init", QUIET),
    ("git", "add", "-A"),
    (*_GIT, "commit", QUIET, "-m", "start", "--allow-empty"),
)
_CLAUDE: Final = ("claude", "-p")
_OPTIONS: Final = (
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
)
_TIMED_OUT: Final = '{"type": "timeout"}'


def run(
    command: Sequence[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    # Fixed commands of this script: claude, pytest, mypy, git, uv
    return subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def environment(workdir: Path) -> dict[str, str]:
    """
    A virtual environment with nuke-di built from this checkout and what the tasks need, shared by every run.
    """
    venv = _venv(workdir, _wheel(workdir))
    env = _inherited()
    env["VIRTUAL_ENV"] = str(venv)
    binaries = str(venv / "bin")
    env["PATH"] = os.pathsep.join((binaries, env["PATH"]))
    return env


def _inherited() -> dict[str, str]:
    """
    The environment of this script, without its virtual environment and its uv and Python settings.
    """
    inherited = os.environ.items()
    return {name: setting for name, setting in inherited if not name.startswith(_OWN_SETTINGS)}


def _wheel(workdir: Path) -> Path:
    wheels = workdir / "wheels"
    shutil.rmtree(wheels, ignore_errors=True)
    build = ("uv", "build", "--wheel", QUIET, "-o", str(wheels), str(ROOT))
    subprocess.run(build, check=True)  # noqa: S603
    return next(wheels.glob("nuke_di-*.whl"))


def _venv(workdir: Path, wheel: Path) -> Path:
    venv = workdir / "venv"
    if not venv.exists():
        create = ("uv", "venv", QUIET, "--python", PYTHON, str(venv))
        subprocess.run(create, check=True)  # noqa: S603
    python = str(venv / "bin" / "python")
    install = ("uv", "pip", "install", QUIET, "--reinstall-package", "nuke-di", "--python", python)
    subprocess.run((*install, f"{wheel}[fastapi]", *_PACKAGES), check=True)  # noqa: S603
    return venv


def prepare(task: Task, condition: str, project: Path) -> None:
    project.mkdir(parents=True)
    (project / "pytest.ini").write_text(_PYTEST_INI)
    if task.example is not None:
        shutil.copytree(
            ROOT / "examples" / task.example,
            project / task.example,
            ignore=shutil.ignore_patterns("README.md", "__pycache__"),
        )
    if condition == "agents-md":
        (project / "CLAUDE.md").write_text(agents_md_block())
    for command in _START:
        subprocess.run(command, cwd=project, check=True, capture_output=True)  # noqa: S603


def agent(
    task: Task,
    condition: str,
    project: Path,
    env: dict[str, str],
    model: str,
) -> str:
    plugin = ("--plugin-dir", str(ROOT)) if condition == "skill" else ()
    command = (*_CLAUDE, task.prompt, "--model", model, *_OPTIONS, *plugin)
    try:
        return run(command, project, env, timeout=_AGENT_TIMEOUT_SECONDS).stdout
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
        return f"{stdout}\n{_TIMED_OUT}\n"
