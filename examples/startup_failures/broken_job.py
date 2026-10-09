import logging

from nuke_di import job

from startup_failures.resolution import Checkout

if __name__ == "__main__":  # a program configures logging; a test that imports the job does not
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s: %(message)s")


@job  # type: ignore[nuke-di]  # the broken tree is the point of this example, and mypy reports it too
async def checkout(checkout: Checkout) -> None:
    print("checkout: never runs, Cache cannot be built")
