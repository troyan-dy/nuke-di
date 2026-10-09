from decimal import Decimal
from unittest.mock import call

import pytest
from faststream import TestApp
from faststream.nats import TestNatsBroker
from nuke_di import DI, Dependencies

from faststream_nats.app import app, broker, handle_order, print_receipt
from faststream_nats.clients import Database
from faststream_nats.messages import Order
from faststream_nats.publish import Nats, publish


class FakeDatabase(Database):
    async def fetch_customer(self, customer_id: int) -> str:
        return "tester"


async def test_order_is_forwarded_as_receipt(capsys: pytest.CaptureFixture[str]) -> None:
    # The test broker runs no app hooks: TestApp starts the app, and with it the clients
    with DI.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker) as test_broker, TestApp(app):
            for order_id, amount in [(1, "2.50"), (2, "1.25")]:
                order = Order(order_id=order_id, amount=Decimal(amount))
                await test_broker.publish(order, "faststream_nats.orders", headers={"customer-id": "7"})

            handle_order.mock.assert_called_with({"order_id": 2, "amount": "1.25"})
            print_receipt.mock.assert_called_with({"order_id": 2, "customer": "tester", "total": "3.75"})

    out = capsys.readouterr().out
    assert "order 1: tester paid 2.50" in out
    assert "receipt 2: tester has paid 3.75 in total" in out
    assert "database: connected" not in out  # a Replacement is never connected


async def test_publish_sends_count_orders(di: Dependencies) -> None:
    nats = di.mock(Nats)
    injected = di.inject(publish)
    async with di:
        await injected(count=2, customer_id=5)

    assert nats.send_order.await_args_list == [
        call(Order(order_id=1, amount=Decimal("9.99")), 5),
        call(Order(order_id=2, amount=Decimal("9.99")), 5),
    ]
