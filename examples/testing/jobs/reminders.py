from typing import Annotated

from nuke_di import Option, job

from testing.clients import Database, Mailer


@job
async def reminders(
    db: Database,
    mailer: Mailer,
    limit: Annotated[int, Option(help="Remind at most this many users")] = 100,
    dry_run: Annotated[bool, Option(help="Print the reminders, send nothing")] = False,
) -> None:
    """Remind the users who have not confirmed their email."""
    emails = await db.unconfirmed(limit)
    for email in emails:
        if dry_run:
            print(f"reminders: would remind {email}")
        else:
            await mailer.send(email, "Please confirm your email")
    print(f"reminders: {len(emails)} users")
