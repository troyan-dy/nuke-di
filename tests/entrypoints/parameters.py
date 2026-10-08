import datetime
import enum
from typing import Annotated

from nuke_di import Client, Option, job


class Db(Client):
    async def connect(self) -> None:
        print("connected", flush=True)


class Mode(enum.Enum):
    FULL = "full"
    DIFF = "diff"


@job
async def parameters(
    db: Db,
    day: Annotated[datetime.date, Option(help="Day to sync", short="d")],
    mode: Mode = Mode.DIFF,
    tables: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    """Sync one day."""
    print(day, mode.name, tables, dry_run, flush=True)
