import asyncio
import itertools
from dataclasses import dataclass

from nuke_di import Client


@dataclass
class User:
    email: str
    confirmed: bool = False


class Database(Client):
    def __init__(self) -> None:
        self._users: dict[int, User] = {}
        self._ids = itertools.count(1)

    async def connect(self) -> None:
        for user in [User("alice@example.com", confirmed=True), User("bob@example.com"), User("carol@example.com")]:
            self._users[next(self._ids)] = user
        print("database: connected")

    async def disconnect(self) -> None:
        print("database: disconnected")

    async def add_user(self, email: str) -> int:
        user_id = next(self._ids)
        self._users[user_id] = User(email)
        return user_id

    async def unconfirmed(self, limit: int) -> list[str]:
        return [user.email for user in self._users.values() if not user.confirmed][:limit]


class Mailer(Client):
    async def connect(self) -> None:
        print("mailer: connected")

    async def disconnect(self) -> None:
        print("mailer: disconnected")

    async def send(self, to: str, subject: str) -> None:
        print(f"mailer: {subject!r} to {to}")


class Queue(Client):
    """
    Signups waiting to be registered; a stand-in for a message broker.
    """

    def __init__(self) -> None:
        self._ids = itertools.count(1)

    async def connect(self) -> None:
        print("queue: connected")

    async def disconnect(self) -> None:
        print("queue: disconnected")

    async def get(self) -> str:
        await asyncio.sleep(0.5)  # waiting for the next message
        return f"user{next(self._ids)}@example.com"


class Signups(Client):
    def __init__(self, db: Database, mailer: Mailer) -> None:
        self._db = db
        self._mailer = mailer

    async def register(self, email: str) -> int:
        user_id = await self._db.add_user(email)
        await self._mailer.send(email, "Welcome")
        return user_id
