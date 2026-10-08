# FastAPI handlers get clients from plain type hints, through a route class that rewrites signatures in place

A FastAPI path operation (and any dependency function it uses) declares a client the way a job does, by its type: `async def get_user(user_id: int, users: UserService)`. A route class (`ClientRoute` for the global `DI`, one per container) runs before FastAPI analyses the endpoint and sets `__signature__` on the function, replacing every client argument with `Annotated[UserService, Depends(<getter>)]`; FastAPI then injects it like any other dependency. A route only *records* its clients; the lifespan installed by `setup(app)` resolves the clients of the routes the app serves and connects the container on startup, and disconnects it on shutdown.

## Considered Options

- **A marker type** (`users: Inject[UserService]`, as in dishka's `FromDishka[T]`): explicit and needs no route class, but every handler repeats it, which is the verbosity this integration exists to remove.
- **A wrapper function** with a new signature instead of mutating the user's: FastAPI keys `app.dependency_overrides` by the dependency callable, so wrapping a dependency function silently disables the overrides users already have in their tests; a wrapper also has to re-implement generator delegation (`athrow` into a `yield` dependency) by hand.
- **Patching FastAPI internals** (`analyze_param`, `get_dependant`) so that no route class is needed: works for every router, but breaks on any internal refactor — FastAPI 0.142 already replaced route copying in `include_router` with lazy route groups.
- **Resolving clients when the route is declared**, like `inject()`: nothing to keep until startup, but importing the app module would build the client tree, and a test could no longer `override()` a client before starting the app.
- **Finding the clients by walking `app.routes` on startup**: routes of included routers are only reachable through FastAPI's private `_IncludedRouter` since 0.142. nuke-di records the includes itself instead, through `ClientRouter.include_router()` and the `app.include_router()` that `setup()` wraps.
- **One list of clients per container**: simple, but a startup would also connect the routes of other apps and of apps already dropped, which FastAPI's own caches keep alive.

## Consequences

- `__signature__` of a user function changes. Calling the function directly is unaffected; only introspection sees the `Depends`. Declared with another container, a function is rewritten again from its original signature; routes keep the dependencies they captured.
- Every router must be a `ClientRouter` (or use `route_class=ClientRoute`); `setup(app)` covers the app itself. Forgetting it is caught by `NotSingletonClient.__get_pydantic_core_schema__`, which turns pydantic's schema error into a message that names the fix.
- FastAPI 0.14x applies router-, include- and app-level dependencies to included routes lazily, outside the route class. `ClientRouter` rewrites its own and its `include_router()` dependencies; `setup()` rewrites the app's and wraps `include_router()` of that app instance.
- A class used as a dependency gets its rewritten `__signature__` through a descriptor that returns it for that class only, since subclasses would inherit a plain attribute.
- Websocket endpoints: FastAPI builds `APIWebSocketRoute` without the route class, so `ClientRouter` and the app that `setup()` was given override `add_api_websocket_route()`, which every websocket declaration goes through, and rewrite the endpoint before FastAPI reads it. A websocket on a plain `APIRouter(route_class=ClientRoute)` is not covered.
- FastStream builds subscribers with fast-depends, which reads `inspect.signature()` as FastAPI does; `nuke_di.faststream` shares this rewrite (`nuke_di._integration`) with `faststream.Depends` as the marker. Litestar matches dependencies by name instead, see ADR-0004.
- A router included into a plain `APIRouter` rather than a `ClientRouter` is invisible on FastAPI 0.14x: its requests fail with "not connected".
