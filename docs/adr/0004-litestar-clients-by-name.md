# Litestar handlers get clients through app-level providers named after their arguments

A Litestar route handler declares a client by its type, `async def get_user(user_id: int, users: UserService)`, like a FastAPI path operation. Litestar 2 injects dependencies by name only, so `ClientPlugin`, in `on_app_init`, finds every argument typed as a client in the handlers the app is created with and in every dependency declared on their layers, and adds one `Provide(<getter>)` per argument name to the app's dependencies. It rewrites each such argument's annotation in place to an explicit dependency without validation (`Annotated[UserService, Dependency(), SkipValidationMarker()]` on Litestar 2.23+). One name means one client in the whole app; two clients under one name raise `TypeError` when the app is created. The clients are resolved and connected on startup, as in FastAPI.

## Considered Options

- **A provider per handler**, so the same name could mean different clients in different handlers: Litestar resolves every dependency of a layer for every handler below it and builds a provider's signature model once, with the dependency names of the first handler that resolves it. A provider declared on the app or a router that takes `db: Database` would then fail for every handler that does not itself provide `db`, including the OPTIONS handlers Litestar adds.
- **Providers keyed by name, without rewriting annotations**: works in Litestar 2, but 2.23 warns on every such argument ("Inferred dependency field") and 3.0 announces that inferred dependencies stop working.
- **A marker per argument** (`users: NamedDependency[UserService]` plus a provider): what Litestar asks for, but every handler repeats it, which is what the integration exists to avoid.
- **A custom route handler class** overriding `on_registration`: would see each handler as it is registered, but every handler would have to be declared with it, and Litestar resolves the dependencies inside that same call.
- **Wrapping handler functions** in new functions with new signatures: Litestar keys nothing by the function, but controllers bind methods to their instances on registration, and a direct call of the handler in a test would no longer be the user's function.

## Consequences

- `__annotations__` of a user function changes; `get_type_hints()` sees `Annotated[UserService, ...]`, which strips to `UserService` without `include_extras`. The rewrite does not depend on the container, so apps with different containers share it.
- An app-wide name space for clients: `users: UserService` and `users: Billing` cannot coexist in one app. A user dependency of the same name wins at its layer, like any Litestar dependency.
- A `NotSingletonClient` is one instance per argument name, not per argument.
- Websocket listeners take no clients: Litestar parses their signature when they are declared. Handlers registered after the app is created are not seen.
- When Litestar 3 ships injection by type, the providers can move to it and the annotation rewrite can go.

## Revisited on 2026-10-10 (#75)

Litestar 3 is not released: 2.24.0 is the latest release, with no 3.0 pre-release. Its announcement (litestar.dev, 2026-07-26) plans `TypeDependency[T]` next to `NamedDependency[T]`, with providers keyed by the type, and Litestar 2.24 warns that dependencies inferred by name alone "will stop working in Litestar 3.0". The annotation this integration writes is the explicit form, `Dependency()` being the marker of `NamedDependency`, and 2.24 warns about none of it, so nothing changes before 3.0. The decision is taken again when its API is final: providers keyed by the client class would drop the app-wide name space and the per-name `NotSingletonClient`. A provider per `(handler, argument)` under a synthetic name, which would make a `NotSingletonClient` one per argument in Litestar 2 already at the cost of one provider per argument, is not pursued: `NotSingletonClient` is slated for removal ([ADR-0006](0006-clients-live-as-long-as-the-container.md)). The differences from FastAPI are listed for users in `docs/guide/litestar.md`.
