import asyncio
import logging

from nuke_di import Dependencies

from graph.api import place_order

logging.basicConfig(level=logging.DEBUG, format="%(levelname)-5s %(message)s")
logging.getLogger("asyncio").setLevel(logging.WARNING)  # keep only the records of nuke_di


async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(place_order)
    async with deps:  # the DEBUG log names every client as it starts, with how many have connected
        print("placed order", await injected(42))


if __name__ == "__main__":
    asyncio.run(main())
