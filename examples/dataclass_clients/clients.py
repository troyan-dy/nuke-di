import itertools

from nuke_di import Client


class Postgres(Client):
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")

    async def insert_order(self, user: str, amount: int) -> int:
        order_id = next(self._ids)
        print(f"postgres: order {order_id} of {user} for {amount}")
        return order_id

    async def mark_paid(self, order_id: int, charge_id: str) -> None:
        print(f"postgres: order {order_id} paid by {charge_id}")


class Redis(Client):
    async def connect(self) -> None:
        print("redis: connected")

    async def disconnect(self) -> None:
        print("redis: disconnected")

    async def delete(self, key: str) -> None:
        print(f"redis: deleted {key}")


class PaymentGateway(Client):
    async def charge(self, amount: int) -> str:
        return f"ch_{amount}"


class Mailer(Client):
    async def send(self, to: str, text: str) -> None:
        print(f"mailer: to {to}: {text}")
