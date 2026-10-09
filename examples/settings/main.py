from nuke_di import job

from settings.clients import Cache, Database, Settings


@job
async def main(settings: Settings, db: Database, cache: Cache) -> None:
    print(f"main: {settings}")
    print(f"main: one Settings for everyone: {db.settings is cache.settings is settings}")
