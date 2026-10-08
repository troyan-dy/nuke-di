# FastAPI integration

Status: accepted.
Issue: [#12](https://github.com/troyan-dy/nuke-di/issues/12) (the FastAPI part; Litestar and FastStream are left for later).
Decision record: [ADR-0003](../adr/0003-fastapi-signature-rewrite.md).
Terms: see [CONTEXT.md](../../CONTEXT.md). This spec uses **Client**, **Container**, **Resolution**, **Replacement** and **Override** as defined there.

## Problem

A service that runs `@worker`s and `@job`s on `nuke-di` usually has an HTTP API too. Today its clients have to be connected by hand in the FastAPI lifespan, and `DI.inject(handler)` does not work as an endpoint: the `functools.partial` it returns still shows every client in its signature, so FastAPI tries to read `users: UserService` from the request.

## Goal

A path operation takes a client the way a job does, by its type, with nothing else to write per handler:

```python
from fastapi import FastAPI

from nuke_di.fastapi import setup

app = FastAPI()
setup(app)


@app.get("/users/{user_id}")
async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)
```

The clients connect, layer by layer, when the app starts and disconnect when it stops.

## Non-goals

- **Websocket endpoints.** FastAPI builds them without the route class (see ADR-0003); they keep using FastAPI dependencies.
- **Request scopes.** A client is a singleton per container, or one instance per argument for a `NotSingletonClient`, exactly as with `inject()`. Per-request clients are a separate decision.
- **Litestar, FastStream, other frameworks.** Later, each in its own module.
- **Changing `inject()`.**

## Public API

Module `nuke_di.fastapi`, installed with the `fastapi` extra (`pip install nuke-di[fastapi]`, `fastapi>=0.100`). `import nuke_di` never imports FastAPI.

### `setup(app, container=DI)`

1. Sets `app.router.route_class` to the route class of `container`. Raises `TypeError` if the app already has a route class that does not derive from it, since it would be silently replaced.
2. Wraps `app.router.lifespan_context`. On startup: resolve every client registered by a route of `container`, then `container.connect()`, then enter the app's own lifespan, so the app's startup code can use the clients. On shutdown, after the app's own lifespan: set `Shutdown` and stop `BackgroundTasks` if they were resolved, then `container.disconnect()`. A failed startup still disconnects what connected (the container's rollback) and forgets the resolved clients.

Must be called before routes are declared on `app`.

### `ClientRoute` and `client_route(container)`

`ClientRoute` is the route class of the global `DI`; `client_route(container)` returns the one of another container (the same class for the same container). Routers declare it: `APIRouter(route_class=ClientRoute)`.

When a route is created, for the endpoint and for every dependency function reachable from it (`Depends(...)` in a default or in `Annotated`, and the route's `dependencies=`, which include router- and app-level ones), recursively:

- every argument whose type hint is a client, bare or in `Annotated` without a `Depends`, is replaced in the function's `__signature__` by `Annotated[<Client>, Depends(<getter>)]` and registered with the container;
- nothing is resolved: the getter returns the instance resolved on startup.

A function is rewritten once; rewriting it for another container raises `TypeError`.

### Getter

An `async def` with no arguments, so FastAPI calls it inline (no threadpool). Before startup, or after shutdown, it raises `RuntimeError("... clients are not connected: start the app with its lifespan, e.g. with TestClient(app)")`.

### `NotSingletonClient.__get_pydantic_core_schema__`

In core, no import of pydantic. Defers to pydantic's own handler and only replaces its failure with a `TypeError` that explains how to inject the client into FastAPI. This is what a user sees when a router lacks the route class, a websocket endpoint takes a client, or a dependency comes through `include_router(dependencies=...)`.

## Testing

- `with DI.override(Database, fake): with TestClient(app) as client: ...` works: importing the app only registers clients, so the container is still empty when the override starts, and startup resolves with the Replacement.
- `app.dependency_overrides[some_dependency]` keeps working for dependency functions that take clients, because they are rewritten in place rather than wrapped.
- The handler is still a plain function; tests can call it directly with a fake client.

## Decisions from self-grilling

| # | Question | Decision |
|---|---|---|
| 1 | Clients only in endpoints, or in dependency functions too? | Both: auth dependencies typically need a client, and FastAPI would fail on them otherwise. |
| 2 | Mechanism | In-place `__signature__` rewrite to `Annotated[C, Depends(getter)]` (ADR-0003). |
| 3 | Activation | `route_class`: the only stable public hook before FastAPI analyses an endpoint. |
| 4 | When to resolve | On startup, not at route declaration: imports stay cheap, and `override()` works in tests. |
| 5 | How startup finds the clients | A per-container registry filled by the route class, not a walk of `app.routes` (private `_IncludedRouter` in FastAPI 0.142). |
| 6 | Getter cost | `async def` without arguments: about 4 µs per request with one client, measured. |
| 7 | Container choice | `ClientRoute` for `DI`; `client_route(container)` and `setup(app, container)` for others. |
| 8 | Lifespan order | Ours outside the app's own: clients are connected during the app's startup and still connected during its shutdown. Shutdown order follows a Run: `Shutdown`, `BackgroundTasks`, `disconnect()`. |
| 9 | Unsupported places | One `TypeError` with the fix, through the pydantic hook, instead of FastAPI's "Invalid args for response field". |
| 10 | `NotSingletonClient` | One instance per argument, like `inject()`. |
| 11 | Minimum FastAPI | 0.100 (pydantic v2); the prototype passed on 0.100, 0.110, 0.115, 0.120 and 0.142. |
| 12 | FastAPI's `BackgroundTasks` | Not a client; `nuke_di.BackgroundTasks` is. Documented, no special case. |
