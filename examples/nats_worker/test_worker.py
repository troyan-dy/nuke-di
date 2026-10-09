from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from nats.aio.msg import Msg
from nuke_di import ConnectError, Dependencies, Shutdown

from nats_worker.clients import SUBJECT, Nats, Order, Orders
from nats_worker.worker import QUEUE_GROUP, consume, handle


def message(data: bytes) -> Msg:
    # A delivered message without a server: handle() reads only its data
    return Msg(_client=MagicMock(), subject=SUBJECT, data=data)


async def test_handle_saves_the_order() -> None:
    orders = Orders()

    await handle(message(b'{"id": 7, "item": "lamp", "quantity": 2}'), orders)

    assert orders.saved == [Order(id=7, item="lamp", quantity=2)]


async def test_handle_skips_an_invalid_message() -> None:
    orders = Orders()

    await handle(message(b'{"id": "seven"}'), orders)

    assert orders.saved == []


async def test_consume_drains_the_subscription_on_shutdown() -> None:
    subscription = AsyncMock()
    nats = MagicMock()
    nats.connection.subscribe = AsyncMock(return_value=subscription)
    shutdown = Shutdown()
    shutdown.set()  # SIGTERM arrived: consume() returns right after draining

    await consume(nats, Orders(), shutdown)

    nats.connection.subscribe.assert_awaited_once_with(SUBJECT, queue=QUEUE_GROUP, cb=ANY)
    subscription.drain.assert_awaited_once()


async def test_nats_down_fails_the_startup_at_once(di: Dependencies, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATS_URL", "nats://localhost:1")  # nothing listens there
    di.resolve(Nats)

    # No retries: the first refused connection is the error, well before CONNECT_TIMEOUT_SECONDS
    with pytest.raises(ConnectError, match=r"Nats.connect\(\) raised"):
        async with di:
            pass
