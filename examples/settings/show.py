from nuke_di import job

from settings.clients import Cache, Database, Settings


@job
async def show(settings: Settings, db: Database, cache: Cache) -> None:
    print(f"show: {settings}")
    shared = db.settings is settings and cache.settings is settings
    print(f"show: one Settings for everyone: {shared}")
