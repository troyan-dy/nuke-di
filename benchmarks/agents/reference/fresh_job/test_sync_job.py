import datetime

from sync_job import Redis, sync

from nuke_di import Dependencies


async def test_sync(di: Dependencies) -> None:
    injected = di.inject(sync)
    redis = di.resolve(Redis)
    async with di:
        await injected(day=datetime.date(2026, 10, 1))
    assert "2026-10-01:0" in redis.rows
