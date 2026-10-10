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

    async def fetch_user(self, user_id: int) -> str:
        try:
            return self._users[user_id]
        except KeyError:
            raise LookupError(f"user {user_id} not found") from None


class Mailer(Client):
    """
    Stands in for an SMTP connection: a mail is printed.
    """

    async def connect(self) -> None:
        print("mailer: connected")

    async def disconnect(self) -> None:
        print("mailer: disconnected")

    async def send(self, to: str, subject: str) -> None:
        print(f"mail to {to}: {subject}")


class Reports(Client):
    def __init__(self, db: Database, mailer: Mailer) -> None:
        self._db = db
        self._mailer = mailer

    async def send_weekly(self, user_id: int) -> str:
        name = await self._db.fetch_user(user_id)
        await self._mailer.send(name, "your weekly report")
        return name
