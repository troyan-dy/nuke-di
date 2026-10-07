import sys
import types
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from nuke_di import DI, Client, Run, job, worker
from nuke_di.entrypoint import entrypoint_name

DECORATORS = [job, worker]


class Db(Client):
    used = False


class Hook:
    def __init__(self) -> None:
        self.runs: list[Run] = []

    async def on_start(self, run: Run) -> None:
        pass

    async def on_finish(self, run: Run) -> None:
        self.runs.append(run)


@pytest.fixture(autouse=True)
def clean_di() -> Iterator[None]:
    yield
    DI.flush()


@pytest.mark.parametrize("decorator", DECORATORS)
def test_returns_function_unchanged_on_import(decorator: Any) -> None:
    async def entry(db: Db) -> None:
        pass

    assert decorator(entry) is entry
    assert decorator(hooks=[Hook()])(entry) is entry
    assert DI.connect_clients == []


@pytest.mark.parametrize("decorator", DECORATORS)
def test_rejects_sync_function(decorator: Any) -> None:
    def entry() -> None:
        pass

    with pytest.raises(TypeError, match="async def"):
        decorator(entry)
    with pytest.raises(TypeError, match="async def"):
        decorator(hooks=[])(entry)


def as_main(func: Callable[..., Any]) -> Callable[..., Any]:
    func.__module__ = "__main__"
    return func


@pytest.mark.parametrize(("decorator", "kind"), [(job, "job"), (worker, "worker")])
def test_runs_and_exits_under_main(decorator: Any, kind: str) -> None:
    async def entry(db: Db) -> None:
        Db.used = True

    hook = Hook()
    with pytest.raises(SystemExit) as exc_info:
        decorator(hooks=[hook])(as_main(entry))

    assert exc_info.value.code == 0
    assert Db.used
    assert [run.kind for run in hook.runs] == [kind]
    assert DI.connected is False


def test_exit_code_of_failed_run() -> None:
    async def entry() -> None:
        raise RuntimeError("boom")

    with pytest.raises(SystemExit) as exc_info:
        job(as_main(entry))

    assert exc_info.value.code == 1


def test_grace_is_read_when_run_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHUTDOWN_GRACE_SECONDS", "-1")

    async def entry() -> None:
        pass

    with pytest.raises(ValueError, match="shutdown_grace"):
        job(as_main(entry))


def fake_main(monkeypatch: pytest.MonkeyPatch, spec_name: str | None, file: str) -> None:
    main = types.ModuleType("__main__")
    main.__spec__ = types.SimpleNamespace(name=spec_name) if spec_name else None  # type: ignore[assignment]
    main.__file__ = file
    monkeypatch.setitem(sys.modules, "__main__", main)


def test_name_of_module_run_with_dash_m(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_main(monkeypatch, "app.jobs.sync", "/srv/app/jobs/sync.py")

    async def sync() -> None:
        pass

    assert entrypoint_name(as_main(sync)) == f"app.jobs.sync.{sync.__qualname__}"


def test_name_of_file_run_by_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_main(monkeypatch, None, "/srv/app/jobs/sync.py")

    async def sync() -> None:
        pass

    assert entrypoint_name(as_main(sync)) == f"sync.{sync.__qualname__}"


def test_name_of_imported_function() -> None:
    async def sync() -> None:
        pass

    assert entrypoint_name(sync) == "tests.test_entrypoint.test_name_of_imported_function.<locals>.sync"
