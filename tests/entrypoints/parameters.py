import datetime
import enum
import sys
from typing import Annotated

from nuke_di import Client, Option, job


class Db(Client):
    async def connect(self) -> None:
        sys.stdout.write("connected\n")
        sys.stdout.flush()


class Mode(enum.Enum):
    FULL = "full"
    DIFF = "diff"


@job
async def sync_day(
    db: Db,
    day: Annotated[datetime.date, Option(help="Day to sync", short="d")],
    mode: Mode = Mode.DIFF,
    tables: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    """Sync one day."""
    sys.stdout.write(f"{day} {mode.name} {tables} {dry_run}\n")
    sys.stdout.flush()
