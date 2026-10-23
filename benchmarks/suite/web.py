"""
The `fastapi` scenario: one request to a handler that takes a client, against FastAPI without nuke-di. It needs
fastapi and httpx and is skipped without them.
"""

import asyncio
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from importlib.util import find_spec
from typing import Any, TypeAlias

from suite.measure import Measurement

# Seconds per request of `repeat` samples: benchmarks/run.py `request_samples(app, path, repeat)`
_Samples: TypeAlias = Coroutine[Any, Any, list[float]]
RequestSamples: TypeAlias = Callable[[Any, str, int], _Samples]
# The `get()` of an HTTP client
Get: TypeAlias = Callable[[str], Awaitable[object]]


def fastapi_scenario(sizes: list[int], repeat: int, request_samples: RequestSamples) -> Iterator[Measurement]:
    try:
        from fastapi_apps import ROWS
    except ImportError as exc:
        sys.stderr.write(f"fastapi: skipped, {exc.name} is not installed\n")
        return
    if find_spec("httpx") is None:
        sys.stderr.write("fastapi: skipped, httpx is not installed\n")
        return
    for shape, app, path in ROWS:
        samples = asyncio.run(request_samples(app, path, repeat))
        yield Measurement("one request", shape, None, samples)


@asynccontextmanager
async def serving(app: Any, path: str) -> AsyncIterator[Get]:
    """
    The `get` of an HTTP client of the FastAPI `app` through the ASGI transport of httpx, with the lifespan of the
    app running as on a server, after one `GET path` that must succeed.
    """
    import httpx

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://benchmark") as http:
            response = await http.get(path)
            assert response.status_code == HTTPStatus.OK, response.text
            yield http.get
