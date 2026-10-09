import asyncio
import itertools

from nuke_di import DI, Client, NotSingletonClient


class Settings(Client):
    """One for the whole container: every consumer reads the same values."""

    def __init__(self) -> None:
        self.api_url = "https://api.example.com"

    async def connect(self) -> None:
        print("settings: loaded")


class HttpSession(NotSingletonClient):
    """One per consumer: its own headers and request counter, which no other consumer sees."""

    numbers = itertools.count(1)

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.number = next(self.numbers)
        self.headers: dict[str, str] = {}
        self.requests = 0

    async def connect(self) -> None:
        print(f"http session #{self.number}: opened")

    async def disconnect(self) -> None:
        print(f"http session #{self.number}: closed after {self.requests} requests")

    async def get(self, path: str) -> str:
        self.requests += 1
        return f"session #{self.number} GET {self.settings.api_url}{path} {self.headers}"


class Orders(Client):
    def __init__(self, settings: Settings, http: HttpSession) -> None:
        self.settings = settings
        self.http = http
        self.http.headers["X-Caller"] = "orders"  # safe: nobody else holds this session

    async def recent(self) -> str:
        return await self.http.get("/orders?limit=10")


class Payments(Client):
    def __init__(self, settings: Settings, http: HttpSession) -> None:
        self.settings = settings
        self.http = http
        self.http.headers["X-Caller"] = "payments"

    async def balance(self) -> str:
        return await self.http.get("/balance")


async def handler(orders: Orders, payments: Payments) -> None:
    print(await orders.recent())
    print(await payments.balance())


async def main() -> None:
    injected = DI.inject(handler)  # builds Orders and Payments once, each with its own HttpSession
    orders, payments = DI.resolve(Orders), DI.resolve(Payments)
    print("same Settings: ", orders.settings is payments.settings is orders.http.settings)
    print("same HttpSession:", orders.http is payments.http)

    async with DI:
        await injected()
        await injected()  # the same instances again: a NotSingletonClient is per consumer, not per call


if __name__ == "__main__":
    asyncio.run(main())
