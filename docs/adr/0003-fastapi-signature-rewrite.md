# FastAPI handlers get clients from plain type hints, through a route class that rewrites signatures in place

A FastAPI path operation (and any dependency function it uses) declares a client the way a job does, by its type: `async def get_user(user_id: int, users: UserService)`. The route class `ClientRoute` runs before FastAPI analyses the endpoint and sets `__signature__` on the function, replacing every client argument with `Annotated[UserService, Depends(<getter>)]`; FastAPI then injects it like any other dependency. A route only *registers* its clients with the container; the lifespan installed by `setup(app)` resolves all registered clients and connects the container on startup, and disconnects it on shutdown.

## Considered Options

- **A marker type** (`users: Inject[UserService]`, as in dishka's `FromDishka[T]`): explicit and needs no route class, but every handler repeats it, which is the verbosity this integration exists to remove.
- **A wrapper function** with a new signature instead of mutating the user's: FastAPI keys `app.dependency_overrides` by the dependency callable, so wrapping a dependency function silently disables the overrides users already have in their tests; a wrapper also has to re-implement generator delegation (`athrow` into a `yield` dependency) by hand.
- **Patching FastAPI internals** (`analyze_param`, `get_dependant`) so that no route class is needed: works for every router, but breaks on any internal refactor — FastAPI 0.142 already replaced route copying in `include_router` with lazy route groups.
- **Resolving clients when the route is declared**, like `inject()`: no registry needed, but importing the app module would build the client tree, and a test could no longer `override()` a client before starting the app.
- **Finding the clients by walking `app.routes` on startup**: no registry, but routes of included routers are only reachable through FastAPI's private `_IncludedRouter` since 0.142.

## Consequences

- `__signature__` of a user function changes. Calling the function directly is unaffected; only introspection sees the `Depends`. A function can be bound to one container only.
- Every router must be created with `route_class=ClientRoute` (`setup(app)` does it for the app itself). Forgetting it is caught by `NotSingletonClient.__get_pydantic_core_schema__`, which turns pydantic's schema error into a message that names the fix.
- Not covered: websocket endpoints (FastAPI builds `APIWebSocketRoute` without the route class) and dependencies passed to `include_router(dependencies=...)` (FastAPI analyses them lazily, outside the route class).
- Startup resolves every client registered by any route of the container, including routes of routers the app does not include.
