import asyncio

from nuke_di import Client


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.2)  # opening a pool
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
        await asyncio.sleep(0.05)
        print("redis: connected")

    async def disconnect(self) -> None:
        print("redis: disconnected")

    async def invalidate(self, key: str) -> None:
        pass  # a stand-in for DEL key


class Kafka(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        print("kafka: connected")

    async def disconnect(self) -> None:
        print("kafka: disconnected")

    async def send(self, topic: str, value: str) -> None:
        print(f"kafka: {topic} <- {value}")
