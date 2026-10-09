from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket
from nuke_di import DI
from nuke_di.fastapi import ClientRouter, setup

from fastapi_app.clients import UserCache


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Runs inside the connected container: its startup code sees every client connected
    print(f"app: started with {', '.join(timing.name for timing in DI.timings)}")
    yield
    print("app: stopping")  # the clients are still connected here


app = FastAPI(lifespan=lifespan)
setup(app)  # before the routes: clients connect on startup, disconnect on shutdown


@app.get("/users/{user_id}")
async def get_user(user_id: int, cache: UserCache) -> str:
    name = cache.get(user_id)
    if name is None:
        raise HTTPException(status_code=404, detail=f"user {user_id} not found")
    return name


async def current_user(x_user_id: Annotated[int, Header()], cache: UserCache) -> str:
    # A dependency takes clients by type hint, the same way as a route
    name = cache.get(x_user_id)
    if name is None:
        raise HTTPException(status_code=401, detail="unknown X-User-Id")
    return name


account = ClientRouter(prefix="/me")


@account.get("")
async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return f"Hello, {user}!"


@app.websocket("/ws/users")
async def lookup(websocket: WebSocket, cache: UserCache) -> None:
    await websocket.accept()
    async for user_id in websocket.iter_text():
        await websocket.send_text(cache.get(int(user_id)) or "not found")


app.include_router(account)
