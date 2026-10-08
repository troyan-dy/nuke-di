# Litestar integration

Status: implemented.
Issue: [#19](https://github.com/troyan-dy/nuke-di/issues/19), a follow-up of [#12](https://github.com/troyan-dy/nuke-di/issues/12).
Decision record: [ADR-0004](../adr/0004-litestar-clients-by-name.md).
Terms: see [CONTEXT.md](../../CONTEXT.md). This spec uses **Client**, **Container**, **Resolution**, **Replacement** and **Override** as defined there.

## Problem

The FastAPI integration lets a path operation take a client by its type hint. A service on Litestar has to connect its clients by hand in a lifespan and wrap each one in a `Provide` under some name.

## Goal

A route handler takes a client the way a job does, with one plugin per app:

```python
from litestar import Litestar, get

from nuke_di.litestar import ClientPlugin


@get("/users/{user_id:int}")
async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


app = Litestar([get_user], plugins=[ClientPlugin()])
```

The clients connect, layer by layer, when the app starts and disconnect when it stops.

## Non-goals

- **Websocket listeners** (`@websocket_listener`, `WebsocketListener`): Litestar parses their signature when they are declared, before any plugin runs. Refused with a `TypeError`.
- **Handlers registered after the app is created** (`app.register()`): the plugin runs once, on app init.
- **Injection by type in Litestar itself**: Litestar 2 matches dependencies by name only; its 3.0 announces a "type" kind of dependency, which may replace the by-name providers later.

## Public API

Module `nuke_di.litestar`, installed with the `litestar` extra (`pip install nuke-di[litestar]`, `litestar>=2.15`). `import nuke_di` never imports Litestar.

### `ClientPlugin(container=DI)`

An `InitPlugin`. In `on_app_init`:

1. Lays out the handlers the app is created with by registering them into a throwaway `Router`: this expands nested routers, controllers and listeners, copies the handlers, and parses no signature.
2. For every HTTP and `@websocket` handler, visits its function and every dependency declared on the app, its routers, its controller and itself (functions, bound methods and classes; callable objects are left alone). Every argument whose type hint is a client, bare or in `Annotated`:
   - is recorded under its name, unless a dependency of that name is declared on a layer the handler sees, which wins;
   - gets its annotation replaced in place by `Annotated[<Client>, Dependency(), SkipValidationMarker()]` (Litestar 2.23+) or `Annotated[<Client>, Dependency(skip_validation=True)]` (older), so Litestar treats it as an explicit dependency, does not warn about a dependency inferred by name, and does not validate the instance against the type.
3. Adds one `Provide(<getter>)` per recorded name to `app_config.dependencies`, under the app's own dependencies, which win on a clash. A name recorded with two different clients raises `TypeError` naming both places.
4. Inserts a lifespan at position 0 of `app_config.lifespan` that resolves the recorded clients and connects the container, and appends an `on_shutdown` hook that disconnects it. Litestar calls `on_shutdown` hooks after every lifespan exits, so the app's own lifespans, `on_startup` and `on_shutdown` hooks all see connected clients. Shutdown follows a Run: `Shutdown`, `BackgroundTasks`, `disconnect()`. A failed startup behaves as in FastAPI: rolled back, flushed, raised as `RuntimeError`.

A websocket listener with a client argument raises `TypeError` that names `@websocket` instead.

### Getter

The same as in FastAPI: an `async def` with no arguments; before startup or after shutdown it raises `RuntimeError("UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)`")`.

## Testing

`with DI.override(Database, replacement), TestClient(app) as client:` works: creating the app only records clients. The handler function stays callable directly with a Replacement.

## Decisions from self-grilling

| # | Question | Decision |
|---|---|---|
| 1 | Hook | `InitPlugin.on_app_init`: the only plugin hook that runs before Litestar registers the handlers and resolves their dependencies; `on_registration` needs a handler subclass, `receive_route` runs after. |
| 2 | How the client reaches the handler | A `Provide` under the argument name: Litestar 2 has no injection by type. |
| 3 | Where the providers live | On the app, one per name. Litestar resolves every dependency of every layer for every handler below it and builds a provider's signature model once, with the dependency names of the first handler that resolves it; a provider declared on the app or a router whose argument is a client must therefore find that client on the app. |
| 4 | The same name for two clients | `TypeError` when the app is created: Litestar's own model is one dependency per name. |
| 5 | A dependency of the same name declared by the user | Wins, at the layer where it is declared, as any Litestar dependency does. |
| 6 | Annotation | Rewritten in place to an explicit dependency without validation: Litestar 2.23 deprecates dependencies matched by name without a marker, and 3.0 drops them. The rewrite does not depend on the container, so two apps with different containers share it. |
| 7 | Walking the handlers | A throwaway `Router`: it lays out routers, controllers and listeners the way the app will, without mutating the user's objects. |
| 8 | Lifespan order | Connect in the outermost lifespan, disconnect in the last `on_shutdown` hook: Litestar calls shutdown hooks after the lifespans, and they may use clients. |
| 9 | Websocket listeners | Refused: their signature is parsed on declaration, so the rewrite comes too late and Litestar would warn or, in 3.0, fail. |
| 10 | `NotSingletonClient` | One instance per argument name, since one provider serves a name. |
| 11 | Minimum Litestar | 2.15, the first with `InitPlugin`; CI runs the Litestar tests on it. |
