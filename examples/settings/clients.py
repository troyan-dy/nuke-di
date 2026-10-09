import os
from dataclasses import field

from nuke_di import Client, client_dataclass


@client_dataclass(frozen=True)
class Settings(Client):
    """
    The configuration of the process, read from the environment once, when the container builds it.
    """

    # default_factory reads the variable when Settings() is built, not when the module is imported
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "postgresql://localhost/app"))
    pool_size: int = field(default_factory=lambda: int(os.environ.get("POOL_SIZE", "5")))
    debug: bool = field(default_factory=lambda: os.environ.get("DEBUG", "").lower() in {"1", "true", "yes"})

    def __post_init__(self) -> None:
        # A bad value fails the run before any client connects
        if self.pool_size < 1:
            raise ValueError(f"POOL_SIZE must be at least 1, got {self.pool_size}")


class Database(Client):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def connect(self) -> None:
        print(f"database: connected to {self.settings.database_url}, pool of {self.settings.pool_size}")

    async def disconnect(self) -> None:
        print("database: disconnected")


class Cache(Client):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def connect(self) -> None:
        print(f"cache: connected, debug={self.settings.debug}")

    async def disconnect(self) -> None:
        print("cache: disconnected")
