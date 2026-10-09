from nuke_di import Client

from graph.clients import Postgres, Redis


class UserRepository(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg

    async def get(self, user_id: int) -> str:
        return await self.pg.fetch_user(user_id)


class OrderRepository(Client):
    def __init__(self, pg: Postgres, cache: Redis) -> None:
        self.pg = pg
        self.cache = cache

    async def create(self, user: str) -> int:
        return await self.pg.insert_order(user)
