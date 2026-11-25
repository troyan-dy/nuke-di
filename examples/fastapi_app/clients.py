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

    async def fetch_users(self) -> dict[int, str]:
        return dict(self._users)


class UserCache(Client):
    """
    Every user in memory, loaded on connect.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._users: dict[int, str] = {}

    async def connect(self) -> None:
        # Database is a dependency, so it is connected by now
        self._users = await self._db.fetch_users()
        print(f"cache: warmed with {len(self._users)} users")

    async def disconnect(self) -> None:
        print("cache: disconnected")

    def get(self, user_id: int) -> str | None:
        return self._users.get(user_id)
