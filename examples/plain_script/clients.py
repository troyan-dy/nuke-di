import asyncio
import os
import sqlite3
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from nuke_di import Client

DEFAULT_PATH = Path(tempfile.gettempdir()) / "nuke-di-plain-script.sqlite3"


class Sqlite(Client):
    """
    One connection to a SQLite file: opened in connect(), closed in disconnect().
    """

    def __init__(self) -> None:
        # __init__ only stores settings: nothing is opened before connect()
        self.path = Path(os.environ.get("SQLITE_PATH", DEFAULT_PATH))
        self._conn: sqlite3.Connection | None = None

    async def connect(self) -> None:
        # sqlite3 blocks, so every call runs in a thread; check_same_thread=False lets those threads share it
        self._conn = await asyncio.to_thread(sqlite3.connect, self.path, check_same_thread=False)
        print("sqlite: connected")

    async def disconnect(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None
        print("sqlite: disconnected")

    async def execute(self, sql: str, arguments: Sequence[Any] = ()) -> None:
        connection = self._connection
        await asyncio.to_thread(_commit, connection, lambda conn: conn.execute(sql, arguments))

    async def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        connection = self._connection
        await asyncio.to_thread(_commit, connection, lambda conn: conn.executemany(sql, rows))

    async def fetchall(
        self,
        sql: str,
        arguments: Sequence[Any] = (),
    ) -> list[tuple[Any, ...]]:
        return await asyncio.to_thread(lambda: self._connection.execute(sql, arguments).fetchall())

    @property
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Sqlite is not connected")
        return self._conn


def _commit(connection: sqlite3.Connection, statement: Callable[[sqlite3.Connection], object]) -> None:
    with connection as conn:  # one transaction: committed on success, rolled back on error
        statement(conn)


class Customers(Client):
    def __init__(self, db: Sqlite) -> None:
        self._db = db

    async def recreate(self) -> None:
        await self._db.execute("DROP TABLE IF EXISTS customers")
        await self._db.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE)")

    async def add_many(self, customers: Sequence[tuple[str, str]]) -> None:
        await self._db.executemany("INSERT INTO customers (name, email) VALUES (?, ?)", customers)

    async def count(self) -> int:
        rows = await self._db.fetchall("SELECT count(*) FROM customers")
        return int(rows[0][0])
