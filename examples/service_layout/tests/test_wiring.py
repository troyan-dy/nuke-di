from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from nuke_di import Dependencies

from service_layout.api import app
from service_layout.clients import Database
from service_layout.jobs.cleanup import cleanup
from service_layout.workers.outbox import relay


@pytest.mark.parametrize("entrypoint", [relay, cleanup])
def test_entrypoint_resolves(entrypoint: Callable[..., object]) -> None:
    """The worker and the job resolve: every __init__ of their trees runs, nothing connects."""
    Dependencies().inject(entrypoint)


def test_api_starts(global_di: Dependencies) -> None:
    """The app resolves and connects the clients of all its routes on startup; no database is needed."""
    with global_di.override(Database), TestClient(app) as client:
        assert client.get("/health").json() == "ok"
