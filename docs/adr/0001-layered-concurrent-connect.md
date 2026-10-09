# Clients connect concurrently, layer by layer

The container used to connect clients one by one in resolution order, which slowed startup down to the sum of all `connect()` calls. Now clients are grouped into layers by their height in the dependency graph (leaves form layer 0) and every layer connects concurrently, from the deepest layer up; disconnect runs the layers in reverse. A failure in a layer cancels the rest of that layer (fail-fast), and an optional global semaphore (`CONNECT_CONCURRENCY`, 0 = unlimited) caps concurrency.

This drops the implicit guarantee that everything resolved earlier is already connected: only dependencies declared in `__init__` are ordered. We chose this on purpose instead of an opt-in flag or a sequential fallback mode, because a client that relies on the connect order of an unrelated client has a hidden dependency that should be declared, not preserved.

Superseded in part by [ADR-0006](0006-connect-by-own-dependencies.md): clients now connect by their own dependencies, without layers; the principle above stands.
