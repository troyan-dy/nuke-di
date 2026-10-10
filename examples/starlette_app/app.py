from nuke_di import DI
from nuke_di.asgi import lifespan
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from starlette_app.clients import Database, UserService

# The clients the handlers take: they connect, with their dependencies, when the app starts
clients = lifespan(DI, UserService, Database)


async def greet_user(request: Request) -> Response:
    user_id: int = request.path_params["user_id"]
    greeting = await clients.get(UserService).greet(user_id)
    if greeting is None:
        return PlainTextResponse(f"user {user_id} not found", status_code=404)
    return PlainTextResponse(greeting)


async def me(request: Request) -> Response:
    name = await clients.get(Database).fetch_user(int(request.headers.get("X-User-Id", "0")))
    if name is None:
        return PlainTextResponse("unknown X-User-Id", status_code=401)
    return PlainTextResponse(f"You are {name}")


app = Starlette(
    routes=[
        Route("/users/{user_id:int}", greet_user),
        Route("/me", me),
    ],
    lifespan=clients,
)
