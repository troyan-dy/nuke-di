from typing import Annotated

from litestar import Controller, Litestar, get
from litestar.di import NamedDependency, Provide
from litestar.exceptions import NotAuthorizedException, NotFoundException
from litestar.params import FromPath, HeaderParameter
from nuke_di.litestar import ClientPlugin

from litestar_app.clients import Database, UserService


class UserController(Controller):
    path = "/users"

    @get("/")
    async def list_users(self, db: Database) -> list[str]:
        return await db.list_users()

    @get("/{user_id:int}")
    async def greet_user(self, user_id: FromPath[int], users: UserService) -> str:
        greeting = await users.greet(user_id)
        if greeting is None:
            raise NotFoundException(f"user {user_id} not found")
        return greeting


async def current_user(x_user_id: Annotated[int, HeaderParameter(name="X-User-Id")], db: Database) -> str:
    # A dependency takes clients by type hint, the same way as a handler
    name = await db.fetch_user(x_user_id)
    if name is None:
        raise NotAuthorizedException("unknown X-User-Id")
    return name


@get("/me", dependencies={"user": Provide(current_user)})
async def me(user: NamedDependency[str]) -> str:
    return f"You are {user}"


# Clients are provided by argument name: `db` and `users` mean the same client in every handler
app = Litestar([UserController, me], plugins=[ClientPlugin()])
