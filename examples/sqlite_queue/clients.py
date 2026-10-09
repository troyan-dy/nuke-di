import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nuke_di import Client


class Database(Client):
    """
    One sqlite3 connection: the path is read in __init__, the file is opened in connect().

    sqlite3 blocks the event loop for the length of a query, which a local file keeps under a millisecond;
    a queue on a database server takes an async driver, such as asyncpg, wrapped the same way.
    """

    def __init__(self) -> None:
        self.path = Path(os.environ.get("QUEUE_DB", Path(tempfile.gettempdir()) / "nuke-di-sqlite-queue.db"))
        self._connection: sqlite3.Connection | None = None

    async def connect(self) -> None:
        # isolation_level=None: autocommit, every statement is its own transaction
        self._connection = sqlite3.connect(self.path, isolation_level=None)
        print("database: connected")

    async def disconnect(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        print("database: disconnected")

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not connected")
        return self._connection


@dataclass(frozen=True)
class Task:
    id: int
    kind: str
    payload: str


class TaskQueue(Client):
    """
    A queue on one table: put() adds a pending task, claim() takes the oldest one, done() completes it.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def connect(self) -> None:
        # Database is connected first: it is a dependency
        self._db.connection.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " kind TEXT NOT NULL,"
            " payload TEXT NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'pending')"
        )
        print("queue: connected")

    async def disconnect(self) -> None:
        print("queue: disconnected")

    async def put(self, kind: str, payload: str) -> int:
        cursor = self._db.connection.execute("INSERT INTO tasks (kind, payload) VALUES (?, ?)", (kind, payload))
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def claim(self) -> Task | None:
        # One statement, so two consumers on the same file never claim the same task
        row = self._db.connection.execute(
            "UPDATE tasks SET status = 'running'"
            " WHERE id = (SELECT id FROM tasks WHERE status = 'pending' ORDER BY id LIMIT 1)"
            " RETURNING id, kind, payload"
        ).fetchone()
        return None if row is None else Task(*row)

    async def done(self, task_id: int) -> None:
        self._db.connection.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))

    async def pending(self) -> int:
        (count,) = self._db.connection.execute("SELECT count(*) FROM tasks WHERE status = 'pending'").fetchone()
        return int(count)
