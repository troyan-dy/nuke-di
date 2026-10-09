from nuke_di import Client, client_dataclass

from dataclass_clients.clients import Mailer, PaymentGateway, Postgres, Redis


@client_dataclass(frozen=True)
class Payments(Client):
    pg: Postgres
    gateway: PaymentGateway

    async def charge(self, order_id: int, amount: int) -> str:
        charge_id = await self.gateway.charge(amount)
        await self.pg.mark_paid(order_id, charge_id)
        return charge_id


@client_dataclass(frozen=True)
class Checkout(Client):
    """The fields are the dependencies: no __init__ that only copies its arguments to self."""

    pg: Postgres
    cache: Redis
    payments: Payments
    mailer: Mailer

    async def place_order(self, user: str, amount: int) -> int:
        order_id = await self.pg.insert_order(user, amount)
        await self.payments.charge(order_id, amount)
        await self.cache.delete(f"cart:{user}")
        await self.mailer.send(user, f"order {order_id} is paid")
        return order_id
