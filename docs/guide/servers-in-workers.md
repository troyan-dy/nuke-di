# Servers with an explicit lifespan

← [Documentation](../../README.md#documentation)

Use the server or framework to run the application. Wrap the part of its lifespan that needs
clients in the container context. Resolve the service before entering, start the server once
the clients are connected, and stop the server before leaving.

```python
from nuke_di import Dependencies

from app.clients import BookService
from app.server import serve


async def main() -> None:
    deps = Dependencies()
    books = deps.resolve(BookService)
    async with deps:
        await serve(books)  # returns after the server has finished shutting down
```

A framework's own lifespan hook can enter the same context. Keep any in-flight handlers and
background tasks inside that lifespan. Shutdown budgets belong to the server or orchestrator.
If it bounds cleanup, allow time for the server to finish its work and for the container's
[disconnect deadline](configuration.md).

For a simple executable module, see [Executable modules](workers-and-jobs.md). For Starlette,
Quart and aiohttp, see [ASGI and lifespan](asgi.md). Frameworks with a dedicated integration
can use their normal startup commands.
