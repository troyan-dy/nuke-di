# Errors

← [Documentation](../../README.md#documentation)

| Exception | Raised when |
|---|---|
| `InitializeDependencyError` | A client's `__init__` raised |
| `ConnectError` | A client's `connect()` raised |
| `ConnectTimeoutError` | A client's `connect()` exceeded `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError` | A client or a function signature cannot be resolved. See [When the tree cannot be built](clients.md#when-the-tree-cannot-be-built). |
| `CircularDependencyError` | Clients depend on each other in a cycle. A subclass of `InvalidSignatureError`. |
| `RuntimeError` | An operation is invalid for the state of the container, such as resolving after connect or replacing an already resolved client |

`ConnectError` and `InitializeDependencyError` are ordinary exceptions. The original failure
is available as `__cause__`. Frameworks report startup failure through their own lifespan.

nuke-di logs through the standard `logging` module under `nuke_di`. Use `DEBUG` to see the
client lifecycle as it happens. Errors name the failing client or the dependency path.

See [Executable modules](workers-and-jobs.md) for how a decorated module runs.
