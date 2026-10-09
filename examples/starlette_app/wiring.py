"""
Clients for a framework without a nuke-di integration; aiohttp, Sanic or any other needs the same two steps.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from nuke_di import DI
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

Handler = Callable[..., Awaitable[Response]]
Endpoint = Callable[[Request], Awaitable[Response]]


class Wiring:
    """
    Wraps handlers that take clients into plain endpoints, and connects the clients for the life of the app.
    """

    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._injected: dict[Handler, Handler] = {}

    def endpoint(self, handler: Handler) -> Endpoint:
        # On import only the handler is recorded: nothing is resolved before the app starts
        self._handlers.append(handler)

        async def call(request: Request) -> Response:
            return await self._injected[handler](request)

        return call

    @asynccontextmanager
    async def lifespan(self, app: Starlette) -> AsyncIterator[None]:
        # Resolved on every startup: the container forgets its clients on disconnect, and a test replaces
        # a client with override() before the app starts
        self._injected = {handler: DI.inject(handler) for handler in self._handlers}
        async with DI:
            yield
