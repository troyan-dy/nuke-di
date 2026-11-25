# Errors

← [Documentation](../../README.md#documentation)

| Exception | Raised when |
|---|---|
| `InitializeDependencyError` | A client's `__init__` raised |
| `ConnectError` | A client's `connect()` raised |
| `ConnectTimeoutError` | A client's `connect()` exceeded `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError` | A client or a function signature cannot be resolved. See [When the tree cannot be built](clients.md#when-the-tree-cannot-be-built). |
| `CircularDependencyError` | Clients depend on each other in a cycle. A subclass of `InvalidSignatureError`. |
| `RuntimeError` | An operation is invalid for the container's state, such as building a new client after startup has begun, replacing a resolved client, or calling a callable bound before a flush |

`ConnectError` and `InitializeDependencyError` are ordinary exceptions. The original failure
is available as `__cause__`. Frameworks report startup failure through their own lifespan.

nuke-di logs through the standard `logging` module under `nuke_di`. Use `DEBUG` to see the
client lifecycle as it happens. Errors name the failing client or the dependency path.

See [Lifecycle guarantees](lifecycle.md) for cleanup and cancellation, and
[Executable modules](workers-and-jobs.md) for how a decorated module runs.
