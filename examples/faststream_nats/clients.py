from decimal import Decimal

from nuke_di import Client


class Database(Client):
    """
    Stands in for a connection pool: the customers live in a dict.
    """

    def __init__(self) -> None:
        self._customers: dict[int, str] = {}

    async def connect(self) -> None:
        self._customers = {1: "alice", 2: "bob", 3: "carol"}
        print("database: connected")

    async def disconnect(self) -> None:
        print("database: disconnected")

    async def fetch_customer(self, customer_id: int) -> str:
        return self._customers.get(customer_id, "guest")


class Ledger(Client):
    """
    Totals per customer, kept for the life of the process.
    """

    def __init__(self) -> None:
        self._totals: dict[str, Decimal] = {}

    async def connect(self) -> None:
        print("ledger: connected")

    async def disconnect(self) -> None:
        print(f"ledger: disconnected, totals {self.totals()}")

    def record(self, customer: str, amount: Decimal) -> Decimal:
        self._totals[customer] = self._totals.get(customer, Decimal(0)) + amount
        return self._totals[customer]

    def totals(self) -> dict[str, str]:
        return {customer: str(total) for customer, total in self._totals.items()}
