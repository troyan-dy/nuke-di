# A lifespan for apps without dependency injection

Status: implemented.
Issue: [#72](https://github.com/troyan-dy/nuke-di/issues/72), a follow-up of [#12](https://github.com/troyan-dy/nuke-di/issues/12).
Terms: see [CONTEXT.md](../../CONTEXT.md). This spec uses **Client**, **Container**, **Resolution**, **Replacement**, **Override**, **Shutdown** and **Background task** as defined there.

## Problem

Starlette, Quart, aiohttp and a plain ASGI app have no dependency injection, so the integrations that fill a handler's arguments by type hint do not apply. Such an app writes `async with DI:` in its lifespan by hand, and misses what `nuke_di.integration.running()` does around it: resolving on every startup, so that `override()` works in tests; setting `Shutdown` and stopping the `BackgroundTasks` before the clients disconnect; turning a `ConnectError`, a `SystemExit`, into a `RuntimeError` the server reports as a failed startup.

## Goal

One object is the app's lifespan and the place its handlers take clients from:

```python
from nuke_di import DI
from nuke_di.asgi import lifespan

clients = lifespan(DI, UserService, Database)


async def get_user(request: Request) -> PlainTextResponse:
    users = clients.get(UserService)
    ...


app = Starlette(routes=[...], lifespan=clients)
```

## Non-goals

- **Handler injection.** These frameworks have no dependency injection to plug into; `get()` is called by the handler.
- **Finding the clients.** There is no route table of client arguments to walk, so the clients are listed.
- **A Quart or aiohttp special case.** Quart connects around `startup()` / `shutdown()` of a `Quart` subclass and aiohttp 3.14 takes the object in `cleanup_ctx` as it is; the guide gives the few lines for each.
- **Per-request clients** (ADR-0006).

## Public API

Module `nuke_di.asgi`. It imports no framework, so it has no extra.

### `lifespan(container, *clients) -> Lifespan`

The same as `Lifespan(container, *clients)`. Checks its arguments when it is called, at import time of the app: `container` must be a `Dependencies` (`TypeError` that shows `lifespan(DI, Database)` otherwise, for the call that left it out), and every item of `clients` a client class itself, as `resolve()` takes it, not `Annotated[Database, ...]` (`TypeError` otherwise). A client listed twice is one client. Resolves nothing.

### `Lifespan`

- `clients(app=None)` returns `running(container, bindings)`, an async context manager: it resolves the listed clients, with their dependencies, and connects the container; on exit it sets `Shutdown`, stops the `BackgroundTasks` and disconnects. `app` is ignored: it is what Starlette (`lifespan(app)`) and aiohttp (`cleanup_ctx`) pass. A failed connect raises `RuntimeError("nuke-di clients failed to start: ...")` with the container flushed; a container connected already raises `RuntimeError("nuke-di clients failed to start: the container is already connected")`.
- `clients.get(cls)` returns the client of `cls` resolved on this startup, or its Replacement. Typed `type[CT] -> CT`. A class not listed raises ``RuntimeError("Database is not a client of this lifespan: list it in `lifespan(container, ...)`")``, whether the app runs or not; a listed client before startup or after shutdown raises ``RuntimeError("UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` in Starlette or `async with app.test_app()` in Quart")``: the same object serves every framework, so the message names the test entry of the two the guide tests.

Both messages are the `Framework` of the module (`not_started`, `not_connected`), and `tests/test_asgi.py` runs `nuke_di.integration.testing.check()` on a Starlette app whose endpoint calls the handler with the clients from `get()`.

## Testing

`with DI.override(Database, replacement), TestClient(app) as client:` works: creating `clients` resolves nothing. A Quart app is started with `async with app.test_app()`.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Where a handler takes a client | `clients.get(cls)` on the object the module of the app holds, not `request.state`. `request.state.x` is `Any` to a type checker; a typed state (`Request[State]`, Starlette 1.x) needs a name per client and a `TypedDict` the user keeps in step; Quart and aiohttp have no lifespan state; and a request without the lifespan would fail with Starlette's `AttributeError` instead of the `not_connected` message. The issue proposed `{"di": container}` and `request.state.di.get(...)`, but the container has no public lookup while connected (`resolve()` refuses), so the state would have needed an accessor anyway. |
| 2 | The state the lifespan yields | None, so it composes: the app's own lifespan enters `clients(app)` and yields its own state. No `wrapped=` argument: nesting is two lines and reads as what it does. |
| 3 | A client not listed but connected as a dependency of a listed one | Refused (`not_started`). The list is what the handlers take; a lookup in the container would work until the listed client stops depending on it. |
| 4 | Built on | `Binding` and `running()` of `nuke_di.integration`, so the startup, shutdown and error behaviour is the one every integration has; `get()` reads `Binding.instance` instead of awaiting `Binding.get()`, which is async for frameworks that call it as a dependency. Nothing was added to the kit. |
| 5 | Quart | A recipe: a `Quart` subclass whose `startup()` enters `clients(self)` in one `AsyncExitStack` before `super().startup()`, and closes it when that fails, and whose `shutdown()` closes it after `super().shutdown()`. Quart 0.22 runs the `before_serving` and `after_serving` hooks each in registration order and skips the rest when one raises, so a pair of hooks of the app gets two cases wrong, both reproduced: a `disconnect` in `after_serving` runs before the app's own `after_serving` hooks registered after it, which find the clients disconnected, and a `before_serving` hook that fails after `connect` leaves the container connected, since Quart then calls no `after_serving` hook, so the next start fails with `the container is already connected`. `startup()` and `shutdown()` run every hook, so the subclass is right whichever hook fails. `while_serving` calls the generator function once, when it is registered (`self.while_serving_gens.append(func())`, Quart 0.22.0), so a second startup in the same process gets `StopAsyncIteration`, which the second test that starts the app hits. Supporting `app.while_serving(clients)` directly would need an object that is an async generator as well, which Quart's types (`Callable[[], AsyncGenerator[None, None]]`) reject. |
| 6 | aiohttp | `app.cleanup_ctx.append(clients)` from aiohttp 3.14, whose `CleanupContext` enters an `AbstractAsyncContextManager` returned by the callback; before 3.14 it needs an async generator, three lines in the guide. |
| 7 | An extra | None: the module imports no framework. Starlette is in the dev dependencies through FastAPI already, so the tests need nothing new. |
| 8 | The README | The issue asked for a `Starlette(lifespan=lifespan(DI, ...))` example in the README. It is in the guide page only, linked from the README "Documentation" list: per AGENTS.md the README is the landing page, and a new feature reaches it only when it belongs in the pitch. |
| 9 | The name `asgi` | The module is named after what most of its users run, a Starlette, Quart or plain ASGI app, and the issue's name for it. Nothing in it is specific to ASGI: aiohttp, which is not ASGI, enters the same async context manager in `cleanup_ctx`, and the guide covers it on the same page rather than under a module of its own. |
| 10 | The example | `examples/starlette_app` moves from a hand-written `Wiring` class (`inject()` per handler and `async with DI`) to `lifespan()`: Starlette is no longer a framework without an integration. |
