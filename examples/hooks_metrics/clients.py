import asyncio

from nuke_di import Client


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")

    async def orders(self, limit: int) -> list[str]:
        return [f"order-{n}" for n in range(1, limit + 1)]


class Kafka(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.6)  # a slow broker: more than half of CONNECT_TIMEOUT_SECONDS=1
        print("kafka: connected")

    async def disconnect(self) -> None:
        await asyncio.sleep(0.05)
        print("kafka: disconnected")

    async def send(self, topic: str, value: str) -> None:
        print(f"kafka: {topic} <- {value}")
