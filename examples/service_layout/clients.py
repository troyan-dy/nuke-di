"""
The clients of the whole service: the API, the worker and the job each take the ones they need.
"""

import contextlib
import datetime
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from typing import Any

from nuke_di import Client

from service_layout.models import Order, OutboxRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL,
    quantity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
"""


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class Settings(Client):
    """
    The configuration from the environment; a test passes the values instead.
    """

    def __init__(self, db_path: str | None = None, poll_seconds: float | None = None) -> None:
        default_path = os.path.join(tempfile.gettempdir(), "service_layout.db")
        self.db_path = os.environ.get("SERVICE_DB", default_path) if db_path is None else db_path
        if poll_seconds is None:
            poll_seconds = float(os.environ.get("OUTBOX_POLL_SECONDS", "1"))
        self.poll_seconds = poll_seconds


class Database(Client):
    """
    SQLite keeps the example self-contained; a real service wraps its Postgres driver the same way. The
    sqlite3 calls block the event loop for the length of a query on a local file; an async driver, such as
    asyncpg, does not.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._conn: sqlite3.Connection | None = None

    async def connect(self) -> None:
        self._conn = sqlite3.connect(self._settings.db_path)
        self._conn.executescript(SCHEMA)
        print("database: connected")

    async def disconnect(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        print("database: disconnected")

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        with self._conn:  # commits on success, rolls back on an exception
            yield self._conn


class Broker(Client):
    """
    A stand-in for Kafka or NATS: connect() would start the producer, publish() would send.
    """

    async def connect(self) -> None:
        print("broker: connected")

    async def disconnect(self) -> None:
        print("broker: disconnected")

    async def publish(self, topic: str, payload: dict[str, Any]) -> None: ...


class Outbox(Client):
    """
    Events written in the transaction of the change they describe, and relayed to the broker later.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, conn: sqlite3.Connection, topic: str, payload: dict[str, Any]) -> None:
        """
        Called inside the caller's transaction, so the event is stored if and only if the change is.
        """
        row = (topic, json.dumps(payload), _now())
        conn.execute("INSERT INTO outbox (topic, payload, created_at) VALUES (?, ?, ?)", row)

    async def pending(self, limit: int) -> list[OutboxRow]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT id, topic, payload FROM outbox WHERE sent_at IS NULL ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
        return [_outbox_row(row) for row in rows]

    async def mark_sent(self, row_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute("UPDATE outbox SET sent_at = ? WHERE id = ?", (_now(), row_id))

    async def delete_sent(self, older_than: datetime.timedelta) -> int:
        before = (datetime.datetime.now(datetime.UTC) - older_than).isoformat()
        with self._db.transaction() as conn:
            return conn.execute("DELETE FROM outbox WHERE sent_at IS NOT NULL AND sent_at <= ?", (before,)).rowcount


def _outbox_row(row: tuple[Any, ...]) -> OutboxRow:
    row_id, topic, payload = row
    return OutboxRow(id=row_id, topic=topic, payload=json.loads(payload))


class Orders(Client):
    def __init__(self, db: Database, outbox: Outbox) -> None:
        self._db = db
        self._outbox = outbox

    async def create(self, product: str, quantity: int) -> Order:
        with self._db.transaction() as conn:
            cursor = conn.execute("INSERT INTO orders (product, quantity) VALUES (?, ?)", (product, quantity))
            assert cursor.lastrowid is not None
            order = Order(id=cursor.lastrowid, product=product, quantity=quantity)
            self._outbox.add(conn, "orders", {"event": "order_created", "order_id": order.id})
        return order

    async def get(self, order_id: int) -> Order | None:
        with self._db.transaction() as conn:
            row = conn.execute("SELECT id, product, quantity FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        found_id, product, quantity = row
        return Order(id=found_id, product=product, quantity=quantity)
