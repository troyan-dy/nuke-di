import asyncio

from nuke_di import BackgroundTasks, Client


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
    Every user in memory: loaded on connect, then refreshed in the background.
    """

    refresh_seconds = 5.0

    def __init__(self, db: Database, tasks: BackgroundTasks) -> None:
        self._db = db
        self._tasks = tasks
        self._users: dict[int, str] = {}

    async def connect(self) -> None:
        # Database is in an earlier layer, so it is connected by now
        await self.refresh()
        print(f"cache: warmed with {len(self._users)} users")
        self._tasks.spawn(self._refresh_forever(), name="cache-refresh")

    async def disconnect(self) -> None:
        print("cache: disconnected")

    async def refresh(self) -> None:
        self._users = await self._db.fetch_users()

    async def _refresh_forever(self) -> None:
        # Cancelled on shutdown before any client disconnects
        while True:
            await asyncio.sleep(self.refresh_seconds)
            await self.refresh()
            print(f"cache: refreshed, {len(self._users)} users")

    def get(self, user_id: int) -> str | None:
        return self._users.get(user_id)


class AuditLog(Client):
    """
    Every lookup of a user, in memory: one log for the application, a record per request.
    """

    def __init__(self) -> None:
        self._records: list[dict[str, object]] = []

    def record(self, request_id: str, user_id: int) -> None:
        self._records.append({"request_id": request_id, "user_id": user_id})

    def records(self) -> list[dict[str, object]]:
        return list(self._records)
