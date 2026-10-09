import dataclasses
from unittest.mock import AsyncMock

import pytest
from nuke_di import Dependencies

from dataclass_clients.clients import Mailer, PaymentGateway, Postgres, Redis
from dataclass_clients.services import Checkout, Payments


async def test_place_order_without_a_container() -> None:
    # A dataclass client is an ordinary class: build it with fakes, no container, nothing connects
    pg = AsyncMock(spec=Postgres)
    pg.insert_order.return_value = 7
    gateway = AsyncMock(spec=PaymentGateway)
    gateway.charge.return_value = "ch_test"
    cache, mailer = AsyncMock(spec=Redis), AsyncMock(spec=Mailer)
    checkout = Checkout(pg=pg, cache=cache, payments=Payments(pg=pg, gateway=gateway), mailer=mailer)

    assert await checkout.place_order("bob", 50) == 7

    pg.mark_paid.assert_awaited_once_with(7, "ch_test")
    cache.delete.assert_awaited_once_with("cart:bob")
    mailer.send.assert_awaited_once_with("bob", "order 7 is paid")


def test_fields_are_injected_and_frozen(di: Dependencies) -> None:
    checkout = di.resolve(Checkout)

    assert checkout.payments.pg is checkout.pg  # Postgres is a Client: one instance for both
    with pytest.raises(dataclasses.FrozenInstanceError):
        checkout.pg = Postgres()  # type: ignore[misc]
