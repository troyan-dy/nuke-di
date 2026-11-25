from nuke_di import entrypoint

from testing.clients import Database, Mailer


@entrypoint
async def reminders(db: Database, mailer: Mailer, limit: int = 100, dry_run: bool = False) -> None:
    """Remind the users who have not confirmed their email."""
    emails = await db.unconfirmed(limit)
    for email in emails:
        if dry_run:
            print(f"reminders: would remind {email}")
        else:
            await mailer.send(email, "Please confirm your email")
    print(f"reminders: {len(emails)} users")
