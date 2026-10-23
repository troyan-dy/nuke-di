import asyncio

from nuke_di import Client

# How long each connection takes to open: they differ, so the DEBUG log of start.py shows them finish apart
POSTGRES_SECONDS = 0.2
REDIS_SECONDS = 0.05
KAFKA_SECONDS = 0.1


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(POSTGRES_SECONDS)  # opening a pool
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")

    async def fetch_user(self, user_id: int) -> str:
        return f"user-{user_id}"

    async def insert_order(self, user: str) -> int:
        print(f"postgres: order 1 of {user}")
        return 1


class Redis(Client):
    async def connect(self) -> None:
        await asyncio.sleep(REDIS_SECONDS)
        print("redis: connected")

    async def disconnect(self) -> None:
        print("redis: disconnected")

    async def invalidate(self, key: str) -> None: ...  # a stand-in for DEL key


class Kafka(Client):
    async def connect(self) -> None:
        await asyncio.sleep(KAFKA_SECONDS)
        print("kafka: connected")

    async def disconnect(self) -> None:
        print("kafka: disconnected")

    async def send(self, topic: str, payload: str) -> None:
        print(f"kafka: {topic} <- {payload}")
