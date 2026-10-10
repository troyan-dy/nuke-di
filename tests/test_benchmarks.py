"""
The benchmark suite of benchmarks/ keeps running: CI runs it the same way, as a smoke test.
"""

import asyncio
import importlib
import json
import platform
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

from nuke_di import Client, Dependencies

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
        "connect() + disconnect(), ideal: the critical path",
        "connect() + disconnect(), above the critical path",
    ]
    wall, ideal, above = application
    # The sleeps of the clients are the figure, so no per-client value; the clients sleep for 0.1 s in all
    assert all(row["n"] == 8 and row["per_client"] is None and len(row["samples"]) == 1 for row in application)
    assert wall["median"] > 0.05 and ideal["median"] > 0.05
    assert above["median"] == wall["samples"][0] - ideal["samples"][0]
    wide = [row for row in data["results"] if row["shape"] == "wide: N independent clients"]
    assert any(row["per_client"] is not None for row in wide)


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


def test_compares_slow_connections(tmp_path: Path) -> None:
    pytest.importorskip("dishka")
    out = tmp_path / "connect.json"

    process = run("--repeat", "1", "--only", "connect", "--summary", "--json", str(out), script=COMPARE)

    assert process.returncode == 0, process.stderr
    lines = process.stdout.splitlines()
    assert any(line.startswith("| Startup: 8 clients, connect() of 1–60 ms ") for line in lines)  # noqa: RUF001
    assert any(line.startswith("| Shutdown: the same 8 clients ") for line in lines)

    data = json.loads(out.read_text())
    figures = {(row["library"], row["scenario"], row["shape"]): row for row in data["results"]}
    page = "product page: 21 clients, connect() of 10–300 ms"  # noqa: RUF001
    startup = "startup: connect() of every client"
    shutdown = "shutdown: disconnect() of every client"
    # 21 clients: 0.33 s along the longest chain, 3.37 s one after another
    assert figures["nuke-di", startup, page]["median"] < 1
    assert figures["dependency-injector", startup, page]["median"] < 1
    assert figures["dishka", startup, page]["median"] > 3
    assert figures["wireup", startup, page]["median"] > 3
    assert figures["injector", startup, page]["error"] == "no async lifecycle"
    # dependency-injector stops layer by layer, 0.65 s, where the longest chain is 0.36 s
    assert (
        figures["dependency-injector", shutdown, page]["median"] > figures["nuke-di", shutdown, page]["median"] + 0.15
    )
    # wireup connects concurrently what the application gets concurrently by hand, dishka with its lock off
    gathered = "startup, the root's arguments gathered by hand"
    assert figures["wireup", gathered, page]["median"] < 2
    assert figures["dishka", gathered, page]["median"] < 2


async def test_the_other_containers_connect_on_the_first_get(monkeypatch: pytest.MonkeyPatch) -> None:
    # What docs/benchmarks.md says of laziness: dishka, wireup and dependency-injector connect nothing when the
    # container is created, only when a client is first asked for; nuke-di connects in `connect()`
    pytest.importorskip("dishka")
    monkeypatch.syspath_prepend(str(ROOT / "benchmarks"))
    compare = importlib.import_module("compare")
    connected: list[str] = []

    class Database(Client):
        async def connect(self) -> None:
            connected.append("connect")

    dishka_provider = compare.Provider(scope=compare.Scope.APP)
    dishka_provider.provide(compare.lifecycle(Database))
    dishka = compare.make_async_container(dishka_provider)
    wireup = compare.wireup.create_async_container(injectables=[compare.wireup.injectable(compare.lifecycle(Database))])
    dependency_injector = compare.containers.DynamicContainer()
    dependency_injector.database = compare.providers.Resource(compare.lifecycle(Database))
    deps = Dependencies()
    deps.resolve(Database)

    for get in (partial(dishka.get, Database), partial(wireup.get, Database), dependency_injector.database):
        assert connected == []
        await get()
        assert connected == ["connect"]
        connected.clear()
    await dishka.close()
    await wireup.close()
    await dependency_injector.shutdown_resources()

    await deps.connect()
    assert connected == ["connect"]
    await deps.disconnect()


async def test_dishka_without_its_lock_builds_a_shared_client_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    # What docs/benchmarks.md says of the gathered startup of dishka: its `get()` calls run concurrently only with
    # `lock_factory=None`, and then a client that two of them need is built and connected once per call
    pytest.importorskip("dishka")
    monkeypatch.syspath_prepend(str(ROOT / "benchmarks"))
    compare = importlib.import_module("compare")
    connected: list[int] = []

    class Database(Client):
        async def connect(self) -> None:
            connected.append(id(self))
            await asyncio.sleep(0.01)

    class Users(Client):
        def __init__(self, db: Database) -> None:
            self.db = db

    class Orders(Client):
        def __init__(self, db: Database) -> None:
            self.db = db

    provider = compare.Provider(scope=compare.Scope.APP)
    for cls in (Database, Users, Orders):
        provider.provide(compare.lifecycle(cls))
    container = compare.make_async_container(provider, lock_factory=None)

    users, orders = await asyncio.gather(container.get(Users), container.get(Orders))

    assert users.db is not orders.db
    assert len(connected) == 2
    await container.close()


def test_draws_the_product_page(tmp_path: Path) -> None:
    pytest.importorskip("dishka")
    out = tmp_path / "product-page.svg"

    process = run(
        str(ROOT / "docs" / "benchmarks" / "connect-py3.11.json"),
        str(out),
        script=ROOT / "benchmarks" / "product_page.py",
    )

    assert process.returncode == 0, process.stderr
    svg = out.read_text()
    assert svg.startswith("<svg ") and "ProductPageApi" in svg and "prefers-color-scheme: dark" in svg
    # The picture of the README is the one the committed figures give
    assert svg == (ROOT / "docs" / "product-page.svg").read_text()


def test_rejects_an_unknown_scenario() -> None:
    process = run("--only", "nothing")

    assert process.returncode == 2
    assert "invalid choice: 'nothing'" in process.stderr
