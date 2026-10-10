import sys

from nuke_di import Client

# A server on stdio talks MCP over its stdout, so everything else goes to stderr
BOOKS = {
    "fantasy": ["A Wizard of Earthsea", "The Hobbit", "The Name of the Wind"],
    "poetry": ["Leaves of Grass"],
}


class Database(Client):
    """
    A stand-in for a database driver: the books of the shop, loaded on connect.
    """

    async def connect(self) -> None:
        self.books = {genre: list(titles) for genre, titles in BOOKS.items()}
        print("database: connected", file=sys.stderr)

    async def disconnect(self) -> None:
        print("database: disconnected", file=sys.stderr)

    async def titles(self, genre: str) -> list[str]:
        return self.books.get(genre, [])


class Catalog(Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def count(self, genre: str) -> int:
        return len(await self.db.titles(genre))

    async def genres(self) -> list[str]:
        return sorted(self.db.books)
