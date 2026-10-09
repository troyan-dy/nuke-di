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
    assert any(line.startswith("| resolve(), cold ") and "| mixed, strings " in line for line in lines)
    assert any(line.startswith("| resolve(), second container, classes seen before ") for line in lines)
    assert any(line.startswith("| resolve(), tracemalloc peak ") for line in lines)

    data = json.loads(out.read_text())
    assert data["python"] == platform.python_version()
    assert data["platform"] == platform.platform()
    assert len(data["commit"]) == 40
    assert data["sizes"] == [10] and data["repeat"] == 1
    assert {row["scenario"] for row in data["results"]} == {
        "resolve(), cold",
        "resolve(), second container, classes seen before",
        "resolve(), warm",
        "resolve(), tracemalloc peak",
    }
    cold = next(row for row in data["results"] if row["scenario"] == "resolve(), cold" and row["shape"] == "deep")
    assert cold["n"] == 10 and cold["unit"] == "s" and len(cold["samples"]) == 1
    assert cold["per_client"] == cold["median"] / 10
    # The string-annotation variant of every tree, cold and on a second container; the cache hit has no variant
    shapes = {(row["scenario"], row["shape"]) for row in data["results"]}
    for shape in ("wide", "deep", "mixed"):
        assert ("resolve(), cold", f"{shape}, strings") in shapes
        assert ("resolve(), second container, classes seen before", f"{shape}, strings") in shapes
        assert ("resolve(), warm", f"{shape}, strings") not in shapes
    peak = next(row for row in data["results"] if row["unit"] == "B")
    assert peak["median"] > 0


def test_connects_an_application_shaped_tree(tmp_path: Path) -> None:
    out = tmp_path / "connect.json"

    process = run("--size", "10", "--repeat", "1", "--only", "connect", "--json", str(out))

    assert process.returncode == 0, process.stderr
    data = json.loads(out.read_text())
    application = [row for row in data["results"] if row["shape"].startswith("application: ")]
    assert [row["scenario"] for row in application] == [
        "connect() + disconnect(), wall time",
        "connect() + disconnect(), ideal: the critical path, no layer barriers",
        "connect() + disconnect(), lost at the layer barriers",
    ]
    wall, ideal, lost = application
    # The sleeps of the clients are the figure, so no per-client value; the clients sleep for 0.1 s in all
    assert all(row["n"] == 8 and row["per_client"] is None and len(row["samples"]) == 1 for row in application)
    assert wall["median"] > 0.05 and ideal["median"] > 0.05
    assert lost["median"] == wall["samples"][0] - ideal["samples"][0]
    assert any(row["shape"] == "wide: N in one layer" and row["per_client"] is not None for row in data["results"])


def test_compares_libraries(tmp_path: Path) -> None:
    # The libraries are the `compare` dependency group, a default group of `uv sync`
    pytest.importorskip("dishka")
    out = tmp_path / "compare.json"

    process = run(
        "--size", "10", "--repeat", "1", "--only", "cold", "--only", "warm", "--json", str(out), script=COMPARE
    )

    assert process.returncode == 0, process.stderr
    lines = process.stdout.splitlines()
    assert lines[1].startswith("nuke-di ") and "dishka " in lines[1] and "wireup " in lines[1]
    assert lines[3].startswith("| Library ") and lines[4].startswith("|---")
    assert any(line.startswith("| dependency-injector | warm: the root again") and "| deep " in line for line in lines)
    # The summary after the table: the best per figure in bold, the others with their ratio to it
    assert any(line.startswith("| Lower is better ") for line in lines)
    assert any(line.startswith("| A cached root ") and "**" in line and "×)" in line for line in lines)  # noqa: RUF001
    assert any(line.startswith("| Cold start: the same 10 clients with string annotations ") for line in lines)

    data = json.loads(out.read_text())
    assert set(data["libraries"]) == {"nuke-di", "dishka", "wireup", "dependency-injector", "injector"}
    assert {row["library"] for row in data["results"]} == set(data["libraries"])
    assert {row["scenario"] for row in data["results"]} == {
        "cold: container, registration, root",
        "warm: the root again",
    }
    # Every library reads the string annotations of a tree in a registered module
    assert all(row.get("error") is None for row in data["results"])
    strings = {(row["library"], row["shape"]) for row in data["results"] if row["shape"].endswith(", strings")}
    assert strings == {
        (library, f"{shape}, strings") for library in data["libraries"] for shape in ("wide", "deep", "mixed")
    }


def test_rejects_an_unknown_scenario() -> None:
    process = run("--only", "nothing")

    assert process.returncode == 2
    assert "invalid choice: 'nothing'" in process.stderr
