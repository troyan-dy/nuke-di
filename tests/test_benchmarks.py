"""
The benchmark suite of benchmarks/ keeps running: CI runs it the same way, as a smoke test.
"""

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "benchmarks" / "run.py"
COMPARE = ROOT / "benchmarks" / "compare.py"


def run(*args: str, script: Path = RUN) -> subprocess.CompletedProcess[str]:
    # The interpreter of the tests, with fixed arguments
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, cwd=ROOT, check=False)  # noqa: S603


def test_prints_a_markdown_table_and_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "benchmarks.json"

    process = run("--size", "10", "--repeat", "1", "--only", "resolve", "--only", "memory", "--json", str(out))

    assert process.returncode == 0, process.stderr
    lines = process.stdout.splitlines()
    assert (
        lines[0].startswith("nuke-di ")
        and f"{platform.python_implementation()} {platform.python_version()}" in lines[0]
    )
    assert lines[2].startswith("| Scenario ") and lines[3].startswith("|---")
    assert any(line.startswith("| resolve(), cold ") and "| mixed " in line and "| 10 |" in line for line in lines)
    assert any(line.startswith("| resolve(), tracemalloc peak ") for line in lines)

    data = json.loads(out.read_text())
    assert data["python"] == platform.python_version()
    assert data["platform"] == platform.platform()
    assert len(data["commit"]) == 40
    assert data["sizes"] == [10] and data["repeat"] == 1
    assert {row["scenario"] for row in data["results"]} == {
        "resolve(), cold",
        "resolve(), warm",
        "resolve(), tracemalloc peak",
    }
    cold = next(row for row in data["results"] if row["scenario"] == "resolve(), cold" and row["shape"] == "deep")
    assert cold["n"] == 10 and cold["unit"] == "s" and len(cold["samples"]) == 1
    assert cold["per_client"] == cold["median"] / 10
    peak = next(row for row in data["results"] if row["unit"] == "B")
    assert peak["median"] > 0


def test_compares_libraries(tmp_path: Path) -> None:
    # The libraries are the `compare` dependency group, a default group of `uv sync`
    pytest.importorskip("dishka")
    out = tmp_path / "compare.json"

    process = run("--size", "10", "--repeat", "1", "--only", "warm", "--json", str(out), script=COMPARE)

    assert process.returncode == 0, process.stderr
    lines = process.stdout.splitlines()
    assert lines[1].startswith("nuke-di ") and "dishka " in lines[1] and "wireup " in lines[1]
    assert lines[3].startswith("| Library ") and lines[4].startswith("|---")
    assert any(line.startswith("| dependency-injector | warm: the root again") and "| deep " in line for line in lines)

    data = json.loads(out.read_text())
    assert set(data["libraries"]) == {"nuke-di", "dishka", "wireup", "dependency-injector", "injector"}
    assert {row["library"] for row in data["results"]} == set(data["libraries"])
    assert all(row["scenario"] == "warm: the root again" and row.get("error") is None for row in data["results"])


def test_rejects_an_unknown_scenario() -> None:
    process = run("--only", "nothing")

    assert process.returncode == 2
    assert "invalid choice: 'nothing'" in process.stderr
