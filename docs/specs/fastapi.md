# FastAPI integration

Status: implemented.
Issue: [#12](https://github.com/troyan-dy/nuke-di/issues/12) (the FastAPI part); websocket endpoints in [#19](https://github.com/troyan-dy/nuke-di/issues/19), with [Litestar](litestar.md) and [FastStream](faststream.md).
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

The clients connect, each after its own dependencies, when the app starts and disconnect when it stops.

## Non-goals

- **Request scopes.** A client is a singleton per container, or one instance per argument for a `NotSingletonClient`, exactly as with `inject()`. Per-request clients are not added: [ADR-0006](../adr/0006-clients-live-as-long-as-the-container.md).
- **Litestar, FastStream.** Each in its own module: [litestar.md](litestar.md), [faststream.md](faststream.md).
- **Changing `inject()`.**

## Public API

Module `nuke_di.fastapi`, installed with the `fastapi` extra (`pip install nuke-di[fastapi]`, `fastapi>=0.105`). `import nuke_di` never imports FastAPI.

### `setup(app, container=DI)`

1. Sets `app.router.route_class` to the route class of `container`. Raises `TypeError` if the app already has a route class that does not derive from it, since it would be silently replaced.
2. Wraps `app.router.lifespan_context`. On startup: refuse a container that is already connected, e.g. by another app, without touching it; resolve the clients of the routes the app serves (see "Which clients start"), then `container.connect()`, then enter the app's own lifespan, so the app's startup code can use the clients. On shutdown, after the app's own lifespan: set `Shutdown` and stop `BackgroundTasks` if they were resolved, then `container.disconnect()`. A failed startup still disconnects what connected (the container's rollback), flushes what was resolved, and forgets the resolved clients. A `ConnectError` or `InitializeDependencyError` is raised as a `RuntimeError` from it: both are `SystemExit`, which would escape the server's event loop instead of being reported as a failed startup.
3. Rewrites the app's `dependencies=` and wraps `app.include_router()` of this app, so the `dependencies=` given to it get their clients too, and so a router of another container is refused with a `TypeError`.
4. Raises `TypeError` when called twice for the same app.

Must be called before routes are declared on `app`.

### `ClientRoute` and `ClientRouter`

`ClientRoute` is the route class of the global `DI`; every container has its own one (a subclass, created once per container), which `setup(app, container)` and `ClientRouter(container=container)` use.

`ClientRouter(container=DI, **kwargs)` is an `APIRouter` with that route class. It also rewrites its own `dependencies=` and the `dependencies=` given to its `include_router()`: since FastAPI 0.14x they are applied to the included routes lazily, without the route class. `APIRouter(route_class=ClientRoute)` works for a router that includes no other routers.

When a route is created, for the endpoint and for every dependency reachable from it, a function or a class (whose `__signature__` is set through a descriptor that subclasses do not see), (`Depends(...)` in a default or in `Annotated`, and the route's `dependencies=`, which include the router's and, for routes declared on the app, the app's), recursively:

- every argument whose type hint is a client, bare or in `Annotated` without a `Depends`, is replaced in the function's `__signature__` by `Annotated[<Client>, Depends(<getter>)]`, and the route keeps the client;
- nothing is resolved: the getter returns the instance resolved on startup.
- a type hint that cannot be evaluated, e.g. a name imported under `TYPE_CHECKING` such as the return type of the dependency Strawberry's `GraphQLRouter` wraps the context getter in, is left as written; the other hints are evaluated each on its own, so the clients and the `Depends` of that function are still found.

The route keeps the clients of everything it reaches. A function is rewritten once, whatever the container, and its signature never changes again: FastAPI 0.137+ reads the signatures of included routes lazily, on the app's first request. Each app set up by `setup()` keeps its own `Binding` for every client argument it reaches, resolved in its own container on startup, so an app factory that makes a container per test works, and two apps on two containers serve the same function at once.

`setup()` also rewrites `FastAPI(dependencies=...)`, which FastAPI applies to included routers lazily too.

### Websocket endpoints

FastAPI builds `APIWebSocketRoute` directly, without the route class. Every websocket declaration (`@app.websocket`, `@router.websocket`, `add_api_websocket_route()`, and the copies older FastAPI makes in `include_router()`) goes through `add_api_websocket_route()` of a router, so `ClientRouter` overrides it and `setup()` replaces it on the app's router: the endpoint and the `dependencies=` given to it are rewritten before FastAPI reads them, and the new route keeps their clients like an HTTP route. The router-level dependencies are tracked already. A websocket on a plain `APIRouter(route_class=ClientRoute)` is not covered and fails with the pydantic `TypeError`.

### Which clients start

The routes the app serves: those in `app.router.routes`, those of every router included through `app.include_router()` or `ClientRouter.include_router()`, recursively, plus the clients of the router-, include- and app-level dependencies recorded on the way. nuke-di records the includes itself, because FastAPI 0.142 keeps included routers in private lazy route groups. A router nobody includes starts nothing; a function that FastAPI's caches keep alive does not matter.

### Getter

An `async def`, so FastAPI calls it inline (no threadpool), one per client argument, shared by every container. It takes the connection (`HTTPConnection`: the request or the websocket) and returns the client of the `Binding` that `connection.app` resolved on startup. An app with no `Binding` of its own for the function (one that serves the routes of a set-up app, e.g. through `include_router(api.router)`, which runs `api`'s lifespan; a mounted app; an app without `setup()`) gets the copy that a running app filled, if exactly one did. Otherwise a set-up app whose container is connected raises the "was not started with the app" error, and any other app the "not connected" one. `bind()` writes the getter through `DependsFramework._getter`, an internal field of the kit. Before startup, or after shutdown, it raises `RuntimeError("UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)`")`.

### `NotSingletonClient.__get_pydantic_core_schema__`

In core, no import of pydantic. Defers to pydantic's own handler and only replaces its failure with a `TypeError` that explains how to inject the client into FastAPI. This is what a user sees when a router lacks the route class, a websocket endpoint takes a client, or a dependency comes through `include_router(dependencies=...)`.

## Testing

- `with DI.override(Database, replacement): with TestClient(app) as client: ...` works: importing the app only records clients, so the container is still empty when the override starts, and startup resolves with the Replacement.
- `app.dependency_overrides[some_dependency]` keeps working for dependency functions that take clients, because they are rewritten in place rather than wrapped.
- The handler is still a plain function; tests can call it directly with a Replacement.

## Decisions from self-grilling

| # | Question | Decision |
|---|---|---|
| 1 | Clients only in endpoints, or in dependency functions too? | Both: auth dependencies typically need a client, and FastAPI would fail on them otherwise. |
| 2 | Mechanism | In-place `__signature__` rewrite to `Annotated[C, Depends(getter)]` (ADR-0003). |
| 3 | Activation | `route_class`: the only stable public hook before FastAPI analyses an endpoint. |
| 4 | When to resolve | On startup, not at route declaration: imports stay cheap, and `override()` works in tests. |
| 5 | How startup finds the clients | Each route keeps its clients; nuke-di records which routers the app includes. Not a walk of FastAPI's routes (private `_IncludedRouter` in 0.142), and not a list per container, which would start routes of other apps and of apps already dropped. |
| 6 | Getter cost | `async def` that takes the connection, to find the app it serves; inline, no threadpool. One request with one client: 105 µs median, the same as a plain FastAPI `Depends()` (`benchmarks/run.py --only fastapi`, CPython 3.14, FastAPI 0.142.4). |
| 7 | Container choice | `ClientRoute` and `ClientRouter()` for `DI`; `setup(app, container)` and `ClientRouter(container=...)` for others. No public route-class factory. |
| 8 | Lifespan order | Ours outside the app's own: clients are connected during the app's startup and still connected during its shutdown. Shutdown order follows a Run: `Shutdown`, `BackgroundTasks`, `disconnect()`. |
| 9 | Unsupported places | One `TypeError` with the fix, through the pydantic hook, instead of FastAPI's "Invalid args for response field". |
| 10 | `NotSingletonClient` | One instance per argument, like `inject()`. |
| 11 | Minimum FastAPI | 0.105: the first that works with current pydantic 2 (0.100–0.104 break on `Header()` even without nuke-di). CI runs the FastAPI tests on it. |
| 12 | FastAPI's `BackgroundTasks` | Not a client; `nuke_di.BackgroundTasks` is. Documented, no special case. |
| 13 | A function declared with two containers | Not refused: an app factory with a container per test is common. Until 1.18.1 it was rewritten again from its original signature; FastAPI 0.137+ reads included routes lazily, so since 1.18.2 it is rewritten once and each app picks its own clients per request (ADR-0003, revisited). |
| 14 | Lazy dependency paths of FastAPI 0.14x | `ClientRouter` covers router-level and `include_router()` dependencies, `setup()` the app's and those of `app.include_router()`, by wrapping that method of the app it was given. |
| 16 | Class dependencies | Supported: `Depends(Auth)` with clients in `Auth.__init__` is common in FastAPI. |
| 17 | A router of another container | `TypeError` on `include_router()`: its clients would never start. |
| 15 | A failed startup | A plain `RuntimeError` from the `SystemExit`: uvicorn reports "Application startup failed" and exits with `3`. |
| 18 | Websocket endpoints (#19) | Through `add_api_websocket_route()`, which every websocket declaration calls: overridden in `ClientRouter`, replaced on the app's router by `setup()`, as `include_router()` already is. |
