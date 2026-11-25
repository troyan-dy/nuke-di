import datetime

from nuke_di import entrypoint

from service_layout.clients import Outbox


@entrypoint
async def cleanup(outbox: Outbox, older_than_days: int = 7) -> None:
    """Delete the outbox rows that were sent long enough ago."""
    deleted = await outbox.delete_sent(datetime.timedelta(days=older_than_days))
    print(f"cleanup: deleted {deleted} sent outbox rows older than {older_than_days} days")
