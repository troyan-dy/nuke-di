import datetime

from nuke_di import Client, job


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def rows(self, day: datetime.date) -> list[str]:
        return [f"{day}:{n}" for n in range(3)]


class Redis(Client):
    def __init__(self, pg: Postgres) -> None:
        self._pg = pg
        self.rows: dict[str, str] = {}

    async def connect(self) -> None:
        # Postgres is a dependency, so it is connected by now
        for row in await self._pg.rows(datetime.date.today()):
            self.rows[row] = row
        print("redis: connected")

    async def write(self, rows: list[str]) -> None:
        self.rows.update((row, row) for row in rows)


@job
async def sync(pg: Postgres, redis: Redis, day: datetime.date) -> None:
    await redis.write(await pg.rows(day))
