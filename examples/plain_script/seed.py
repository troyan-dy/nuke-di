import asyncio
from collections.abc import Sequence

from nuke_di import Dependencies

from plain_script.clients import Customers, Sqlite

DEMO_CUSTOMERS = [
    ("Ada Lovelace", "ada@example.com"),
    ("Alan Turing", "alan@example.com"),
    ("Grace Hopper", "grace@example.com"),
    ("Edsger Dijkstra", "edsger@example.com"),
    ("Barbara Liskov", "barbara@example.com"),
]


async def seed(customers: Customers, rows: Sequence[tuple[str, str]]) -> int:
    await customers.recreate()
    await customers.add_many(rows)
    return await customers.count()


async def main() -> None:
    deps = Dependencies()  # the script's own container: nothing it builds leaks into the global DI
    injected = deps.inject(seed)  # builds Customers -> Sqlite, connects nothing yet
    db = deps.resolve(Sqlite)  # the same Sqlite that Customers got: a Client is one per container
    async with deps:
        total = await injected(rows=DEMO_CUSTOMERS)  # the clients are bound by keyword, pass the rest the same way
        print(f"seed: {total} customers in {db.path}")


if __name__ == "__main__":
    asyncio.run(main())
