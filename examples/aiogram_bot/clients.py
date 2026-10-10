from nuke_di import Client


class Database(Client):
    """
    Stands in for a connection pool: the visits of every user live in a dict.
    """

    def __init__(self) -> None:
        self._visits: dict[int, int] = {}

    async def connect(self) -> None:
        print("database: connected")

    async def disconnect(self) -> None:
        print(f"database: disconnected, {len(self._visits)} users")

    async def add_visit(self, user_id: int) -> int:
        self._visits[user_id] = self._visits.get(user_id, 0) + 1
        return self._visits[user_id]

    async def count_users(self) -> int:
        return len(self._visits)


class UserService(Client):
    def __init__(self, db: Database) -> None:
        self._db = db

    async def greet(self, user_id: int, name: str) -> str:
        visits = await self._db.add_visit(user_id)
        return f"Hello, {name}! This is your visit {visits}."

    async def stats(self) -> str:
        return f"{await self._db.count_users()} users so far"
