import asyncio

from nuke_di import DI

from testing.clients import Signups


async def register(email: str, signups: Signups) -> int:
    return await signups.register(email)


async def main(email: str) -> int:
    injected = DI.inject(register)  # code that uses the global DI: tested with `global_di` or `DI.override`
    async with DI:
        return await injected(email)


if __name__ == "__main__":
    print("registered user", asyncio.run(main("dave@example.com")))
