import logging

from nuke_di import entrypoint

from startup_failures.clients import Orders

if __name__ == "__main__":  # a program configures logging; a test that imports the job does not
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s: %(message)s")


@entrypoint
async def publish(orders: Orders) -> None:
    print("publish: never runs, Kafka is down")
