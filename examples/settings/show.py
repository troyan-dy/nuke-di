from nuke_di import job

from settings.clients import Cache, Database, Settings


@job
async def show(settings: Settings, db: Database, cache: Cache) -> None:
    print(f"show: {settings}")
    print(f"show: one Settings for everyone: {db.settings is cache.settings is settings}")
