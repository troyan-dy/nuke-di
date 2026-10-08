from collections.abc import Callable

import pytest

# The inner sessions load the plugin through its `pytest11` entry point, like any project that installs nuke-di
INI = """
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
"""

CLIENTS = """
from nuke_di import DI, Client, Dependencies


class Database(Client):
    pass


class Users(Client):
    def __init__(self, db: Database) -> None:
        self.db = db
"""


Run = Callable[[str], pytest.RunResult]


@pytest.fixture
def run(pytester: pytest.Pytester) -> Run:
    pytester.makeini(INI)

    def run(tests: str) -> pytest.RunResult:
        pytester.makepyfile(test_inner=CLIENTS + tests)
        return pytester.runpytest("-p", "no:cacheprovider")

    return run


def test_di_is_a_fresh_container_per_test(run: Run) -> None:
    result = run(
        """
seen = []


def test_first(di: Dependencies) -> None:
    assert di is not DI
    assert di.clients == {}
    seen.append(di.resolve(Users))


def test_second(di: Dependencies) -> None:
    assert di.clients == {}
    assert di.resolve(Users) is not seen[0]
"""
    )

    result.assert_outcomes(passed=2)


def test_di_left_connected_errors(run: Run) -> None:
    result = run(
        """
async def test_forgets_to_disconnect(di: Dependencies) -> None:
    di.resolve(Users)
    await di.connect()
"""
    )

    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(['*the test left the container of the "di" fixture connected*'])


def test_global_di_is_flushed_before_and_after_test(run: Run) -> None:
    result = run(
        """
def test_leaks_into_global_di() -> None:
    DI.resolve(Users)


def test_starts_clean(global_di: Dependencies) -> None:
    assert global_di is DI
    assert DI.clients == {}
    DI.mock(Database)
    DI.resolve(Users)


def test_after_global_di() -> None:
    assert DI.clients == {}
"""
    )

    result.assert_outcomes(passed=3)


def test_global_di_left_connected_errors(run: Run) -> None:
    result = run(
        """
async def test_forgets_to_disconnect(global_di: Dependencies) -> None:
    DI.resolve(Users)
    await DI.connect()


def test_next_test_starts_clean(global_di: Dependencies) -> None:
    assert not DI.connected
    assert DI.clients == {}
"""
    )

    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*the test left the global DI connected*"])


def test_global_di_left_connected_by_previous_test_errors(run: Run) -> None:
    result = run(
        """
async def test_connects_global_di_without_fixture() -> None:
    DI.resolve(Users)
    await DI.connect()


def test_with_fixture(global_di: Dependencies) -> None:
    pass


def test_next_test_starts_clean(global_di: Dependencies) -> None:
    assert not DI.connected
    assert DI.clients == {}
"""
    )

    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*a previous test left the global DI connected*"])
