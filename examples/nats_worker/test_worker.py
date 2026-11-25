import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from nats.aio.msg import Msg
from nuke_di import ConnectError, Dependencies

from nats_worker.clients import SUBJECT, Nats, Order, Orders
from nats_worker.worker import QUEUE_GROUP, consume, process_order


def message(payload: bytes) -> Msg:
    return Msg(_client=MagicMock(), subject=SUBJECT, data=payload)


async def test_process_order_saves_the_order() -> None:
    orders = Orders()

    await process_order(message(b'{"id": 7, "product": "lamp", "quantity": 2}'), orders)

    assert orders.saved == [Order(id=7, product="lamp", quantity=2)]


async def test_process_order_skips_an_invalid_message() -> None:
    orders = Orders()

    await process_order(message(b'{"id": "seven"}'), orders)

    assert not orders.saved


async def test_consume_drains_the_subscription_on_cancellation() -> None:
    started = asyncio.Event()
    subscription = AsyncMock()

    def subscribe(*args: object, **kwargs: object) -> AsyncMock:
        started.set()
        return subscription

    nats = MagicMock()
    nats.connection.subscribe = AsyncMock(side_effect=subscribe)
    task = asyncio.create_task(consume(nats, Orders()))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    nats.connection.subscribe.assert_awaited_once_with(SUBJECT, queue=QUEUE_GROUP, cb=ANY)
    subscription.drain.assert_awaited_once()


async def test_nats_down_fails_the_startup_at_once(di: Dependencies, monkeypatch: pytest.MonkeyPatch) -> None:
    refused = AsyncMock(side_effect=ConnectionRefusedError("broker unavailable"))
    monkeypatch.setattr(asyncio, "open_connection", refused)
    monkeypatch.setenv("NATS_URL", "nats://broker.test:4222")
    di.resolve(Nats)

    with pytest.raises(ConnectError, match=r"Nats.connect\(\) raised"):
        async with di:
            ...

    refused.assert_awaited_once()
