from nuke_di import Client


class Database(Client):
    """
    Stands in for a connection pool: the users live in a dict.
    """

    def __init__(self) -> None:
        self._users: dict[int, str] = {}

    async def connect(self) -> None:
        self._users = {1: "alice", 2: "bob", 3: "carol"}
        print("database: connected")

    async def disconnect(self) -> None:
        print("database: disconnected")

    async def fetch_user(self, user_id: int) -> str | None:
        return self._users.get(user_id)


class UserService(Client):
    def __init__(self, db: Database) -> None:
        self._db = db

    async def greet(self, user_id: int) -> str | None:
        name = await self._db.fetch_user(user_id)
        return None if name is None else f"Hello, {name}!"
