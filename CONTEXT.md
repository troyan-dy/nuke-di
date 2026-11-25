# nuke-di

Async dependency injection: builds a tree of clients from type hints and drives their connect / disconnect lifecycle.

## Language

**Client**:
A dependency with an async connect / disconnect lifecycle; a singleton within its container. It lives as long as its container: there is no per-request or per-message client (ADR-0006).
_Avoid_: Service, component, provider, scoped client

**Container**:
The `Dependencies` object that resolves clients and owns their lifecycle. A single registry
uses the declared client class as the key for the instance and its lifecycle dependencies.
Disconnect and flush discard the resolved clients and invalidate callable bindings that
capture them. An active Override can register its Replacement again.
_Avoid_: Registry, injector

**Resolution**:
Building a client together with its whole dependency tree, before the container connects.
_Avoid_: Instantiation, wiring

**Connect order**:
A client connects as soon as its own dependencies have and disconnects as soon as its consumers have; only dependencies declared in `__init__` order clients, and every client that is ready connects concurrently with the others.
_Avoid_: Layer, level, DAG schedule

**Disconnect deadline**:
One budget for a container's whole disconnect, and for the rollback of a failed connect, whatever the depth of the tree; each client's `disconnect()` is still bounded by its own timeout. A client still disconnecting when it runs out is timed out, and the clients not started by then are skipped (ADR-0012).
_Avoid_: Shutdown timeout, shutdown deadline

**Integration**:
The glue between nuke-di and a framework: the framework's handlers take clients by type hint through its own dependency injection, and the container connects when the app starts and disconnects when it stops. The framework owns application startup, shutdown and task supervision.
_Avoid_: Plugin, adapter, extension (except where a framework names its own hook so, like Litestar's `ClientPlugin`)

**Replacement**:
An object registered in the container in place of a client class, before that class is resolved; every consumer receives it instead of the client. A Replacement is never connected. Registered with `mock()` it lasts until the next flush; registered with `override()` it lasts until the end of the `with` block.
_Avoid_: Fake, stub, double (for the concept; `mock()` is only the method name)

**Override**:
A `with` block of `override()` that registers a Replacement for its duration and leaves the container flushed.
_Avoid_: Patch

### Executable modules

**Entrypoint**:
An async function marked with `@entrypoint`. Executing its module runs the function with connected clients. Importing the module leaves the original function available to callers.
