import asyncio
import datetime
import os
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nuke_di import Client

DEFAULT_PATH = Path(tempfile.gettempdir()) / "nuke-di-cli-job.sqlite3"


def log(message: str) -> None:
    # Lifecycle and progress go to stderr, so `> report.csv` captures the report alone
    print(message, file=sys.stderr)


class Sqlite(Client):
    """
    One connection to a SQLite file: opened in connect(), closed in disconnect().
    """

    def __init__(self) -> None:
        self.path = Path(os.environ.get("SQLITE_PATH", DEFAULT_PATH))
        self._conn: sqlite3.Connection | None = None

    async def connect(self) -> None:
        # sqlite3 blocks, so every call runs in a thread; check_same_thread=False lets those threads share it
        self._conn = await asyncio.to_thread(sqlite3.connect, self.path, check_same_thread=False)
        log("sqlite: connected")

    async def disconnect(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None
        log("sqlite: disconnected")

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        await asyncio.to_thread(self._commit, lambda conn: conn.execute(sql, params))

    async def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        await asyncio.to_thread(self._commit, lambda conn: conn.executemany(sql, rows))

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        return await asyncio.to_thread(lambda: self._connection.execute(sql, params).fetchall())

    @property
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Sqlite is not connected")
        return self._conn

    def _commit(self, statement: Callable[[sqlite3.Connection], object]) -> None:
        with self._connection as conn:  # one transaction: committed on success, rolled back on error
            statement(conn)


@dataclass(frozen=True)
class Order:
    id: int
    customer: str
    amount: float
    created: datetime.date


DEMO_ORDERS = [
    ("ada", 120.0, "2026-09-28"),
    ("alan", 35.5, "2026-09-29"),
    ("grace", 990.0, "2026-09-30"),
    ("ada", 42.0, "2026-10-01"),
    ("edsger", 7.25, "2026-10-02"),
    ("barbara", 310.0, "2026-10-03"),
    ("alan", 64.0, "2026-10-04"),
    ("grace", 15.0, "2026-10-05"),
]


class Orders(Client):
    def __init__(self, db: Sqlite) -> None:
        self._db = db

    async def ensure_demo_data(self) -> None:
        """
        Create the table and fill it with demo orders when it is empty, so the example runs on its own.
        """
        await self._db.execute(
            "CREATE TABLE IF NOT EXISTS orders "
            "(id INTEGER PRIMARY KEY, customer TEXT NOT NULL, amount REAL NOT NULL, created TEXT NOT NULL)"
        )
        if await self._db.fetchall("SELECT 1 FROM orders LIMIT 1"):
            return
        await self._db.executemany("INSERT INTO orders (customer, amount, created) VALUES (?, ?, ?)", DEMO_ORDERS)
        log(f"orders: the table was empty, inserted {len(DEMO_ORDERS)} demo orders")

    async def since(self, day: datetime.date, limit: int) -> list[Order]:
        rows = await self._db.fetchall(
            "SELECT id, customer, amount, created FROM orders "
            "WHERE created >= ? ORDER BY created DESC, id DESC LIMIT ?",
            (day.isoformat(), limit),
        )
        return [Order(row[0], row[1], row[2], datetime.date.fromisoformat(row[3])) for row in rows]
