import asyncio

from nuke_di import Client


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")


class Kafka(Client):
    """A broker that refuses the connection: connect() raises."""

    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        raise OSError("broker kafka-1:9092 is unreachable")

    async def disconnect(self) -> None:
        print("kafka: disconnected")  # never printed: a client that did not connect is not disconnected


class Search(Client):
    """A cluster behind a firewall that drops the packets: connect() never returns."""

    async def connect(self) -> None:
        await asyncio.sleep(3600)


class Orders(Client):
    def __init__(self, pg: Postgres, kafka: Kafka) -> None:
        self.pg = pg
        self.kafka = kafka

    async def connect(self) -> None:
        print("orders: connected")  # never printed: a dependency failed


class Reports(Client):
    def __init__(self, pg: Postgres, search: Search) -> None:
        self.pg = pg
        self.search = search
