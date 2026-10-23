import asyncio

from nuke_di import Client

# A slow broker: more than half of CONNECT_TIMEOUT_SECONDS=1, which the hook reports
KAFKA_CONNECT_SECONDS = 0.6
KAFKA_DISCONNECT_SECONDS = 0.05


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")

    async def orders(self, limit: int) -> list[str]:
        return [f"order-{number}" for number in range(1, limit + 1)]


class Kafka(Client):
    async def connect(self) -> None:
        await asyncio.sleep(KAFKA_CONNECT_SECONDS)
        print("kafka: connected")

    async def disconnect(self) -> None:
        await asyncio.sleep(KAFKA_DISCONNECT_SECONDS)
        print("kafka: disconnected")

    async def send(self, topic: str, payload: str) -> None:
        print(f"kafka: {topic} <- {payload}")
