from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from starlette_app.clients import Database, UserService
from starlette_app.wiring import Wiring

wiring = Wiring()


async def greet_user(request: Request, users: UserService) -> Response:
    user_id: int = request.path_params["user_id"]
    greeting = await users.greet(user_id)
    if greeting is None:
        return PlainTextResponse(f"user {user_id} not found", status_code=404)
    return PlainTextResponse(greeting)


async def me(request: Request, db: Database) -> Response:
    name = await db.fetch_user(int(request.headers.get("X-User-Id", "0")))
    if name is None:
        return PlainTextResponse("unknown X-User-Id", status_code=401)
    return PlainTextResponse(f"You are {name}")


app = Starlette(
    routes=[
        Route("/users/{user_id:int}", wiring.endpoint(greet_user)),
        Route("/me", wiring.endpoint(me)),
    ],
    lifespan=wiring.lifespan,
)
