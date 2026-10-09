import asyncio

from nuke_di import DI

from dataclass_clients.services import Checkout


async def handler(user: str, amount: int, checkout: Checkout) -> int:
    return await checkout.place_order(user, amount)


async def main() -> None:
    injected = DI.inject(handler)
    print(DI.resolve(Checkout))  # the generated __repr__ lists the injected fields
    async with DI:
        print("placed order", await injected("alice", 120))


if __name__ == "__main__":
    asyncio.run(main())
