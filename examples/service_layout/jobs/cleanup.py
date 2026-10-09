import datetime
from typing import Annotated

from nuke_di import Option, job

from service_layout.clients import Outbox


@job
async def cleanup(
    outbox: Outbox,
    older_than_days: Annotated[int, Option(help="Delete the rows sent at least this many days ago")] = 7,
) -> None:
    """Delete the outbox rows that were sent long enough ago."""
    deleted = await outbox.delete_sent(datetime.timedelta(days=older_than_days))
    print(f"cleanup: deleted {deleted} sent outbox rows older than {older_than_days} days")
