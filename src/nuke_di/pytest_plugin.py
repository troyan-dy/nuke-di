"""
Fixtures for testing code that uses `nuke-di`, registered through the `pytest11` entry point.

Nothing here is autouse: installing `nuke-di` never changes how existing tests run.
"""

from collections.abc import Iterator

import pytest

from nuke_di.core import DI, Dependencies


@pytest.fixture
def di() -> Iterator[Dependencies]:
    """
    A fresh container for one test.
    """
    deps = Dependencies()
    yield deps
    _release(deps, 'the test left the container of the "di" fixture connected')


@pytest.fixture
def global_di() -> Iterator[Dependencies]:
    """
    The global `DI`, flushed before and after the test.
    """
    _release(DI, "a previous test left the global DI connected")
    yield DI
    _release(DI, "the test left the global DI connected")


def _release(deps: Dependencies, reason: str) -> None:
    if not deps.connected:
        deps.flush()
        return

    # Disconnecting needs the event loop of the test, which may be closed by now,
    # so the clients are forgotten instead and the next test starts clean
    deps._abandon()
    # The traceback would only show this plugin, the message says everything
    pytest.fail(f"{reason}; its clients were not disconnected, use `async with` or call disconnect()", pytrace=False)
