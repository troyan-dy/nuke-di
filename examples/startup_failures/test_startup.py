import pytest
from nuke_di import (
    CircularDependencyError,
    Client,
    ConnectError,
    ConnectTimeoutError,
    Dependencies,
    DependenciesSettings,
    InvalidSignatureError,
)

from startup_failures.clients import Orders, Reports
from startup_failures.job import publish
from startup_failures.resolution import Audit, Checkout
from startup_failures.resolution import Orders as CyclicOrders


async def test_connect_error_rolls_back(di: Dependencies) -> None:
    di.resolve(Orders)

    with pytest.raises(ConnectError, match=r"Kafka\.connect\(\) raised OSError") as info:
        await di.connect()

    assert isinstance(info.value.__cause__, OSError)
    assert not di.connected
    outcomes = {t.name: (t.connect_outcome, t.disconnect_outcome) for t in di.timings}
    assert outcomes == {"Postgres": ("ok", "ok"), "Kafka": ("failed", None), "Orders": (None, None)}


async def test_hanging_connect_times_out() -> None:
    deps = Dependencies(settings=DependenciesSettings(connect_timeout=0.1))
    deps.resolve(Reports)

    with pytest.raises(ConnectTimeoutError, match=r"Search did not connect within 0\.1s"):
        await deps.connect()


def test_publish_resolves(di: Dependencies) -> None:
    di.inject(publish)  # the wiring is fine and connects nothing: Kafka fails only in connect()


@pytest.mark.parametrize(
    ("root", "error"),
    [(Checkout, InvalidSignatureError), (Audit, InvalidSignatureError), (CyclicOrders, CircularDependencyError)],
)
def test_resolution_errors(di: Dependencies, root: type[Client], error: type[Exception]) -> None:
    with pytest.raises(error):
        di.resolve(root)
