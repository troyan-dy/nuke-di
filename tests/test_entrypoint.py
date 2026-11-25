import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from nuke_di import Client, entrypoint


def test_import_preserves_function_and_does_not_resolve_clients() -> None:
    class Database(Client):
        def __init__(self) -> None:
            pytest.fail("import must not resolve clients")

    async def main(db: Database) -> None: ...

    assert entrypoint(main) is main


def test_requires_an_async_function() -> None:
    def main() -> None: ...

    with pytest.raises(TypeError, match="async def"):
        entrypoint(main)  # type: ignore[type-var]  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("body", "arguments", "code", "events", "diagnostic"),
    [
        ("    print(db.item, label)", [], 0, ["connect", "data default", "disconnect"], ""),
        ("    raise RuntimeError('boom')", [], 1, ["connect", "disconnect"], "RuntimeError: boom"),
        ("    raise SystemExit(7)", [], 7, ["connect", "disconnect"], ""),
        ("    print(db.item, label)", ["--dry-run"], 1, [], "does not accept command-line arguments"),
    ],
)
def test_runs_as_a_module(
    tmp_path: Path,
    body: str,
    arguments: list[str],
    code: int,
    events: list[str],
    diagnostic: str,
) -> None:
    _write_module(tmp_path, "label: str = 'default'", body)

    completed = _run_module(tmp_path, arguments)

    assert completed.returncode == code
    assert completed.stdout.splitlines() == events
    assert diagnostic in completed.stderr


def test_missing_non_client_argument_is_rejected_before_connect(tmp_path: Path) -> None:
    _write_module(tmp_path, "label: str", "    print(db.item, label)")

    completed = _run_module(tmp_path, [])

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "missing a required argument: 'label'" in completed.stderr


@pytest.mark.parametrize("arguments", [[], ["--dry-run"]])
def test_runs_a_standalone_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    _write_module(tmp_path, "label: str = 'default'", "    print(db.item, label)")
    script = str(tmp_path / "sample_entrypoint.py")
    monkeypatch.setattr(sys, "argv", [script, *arguments])

    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(script, run_name="__main__")

    output = capsys.readouterr().out.splitlines()
    if arguments:
        assert stopped.value.code == "This entrypoint does not accept command-line arguments"
        assert output == []
    else:
        assert stopped.value.code is None
        assert output == ["connect", "data default", "disconnect"]


def _write_module(path: Path, parameter: str, body: str) -> None:
    source = f"""
from nuke_di import Client, entrypoint

class Database(Client):
    item = 'data'

    async def connect(self) -> None:
        print('connect')

    async def disconnect(self) -> None:
        print('disconnect')

@entrypoint
async def main(db: Database, {parameter}) -> None:
{body}

print('after the entrypoint')
"""
    (path / "sample_entrypoint.py").write_text(source, encoding="utf-8")


def _run_module(path: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "sample_entrypoint", *arguments],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
