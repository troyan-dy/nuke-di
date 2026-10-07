# nuke-di

Async dependency injection: builds a tree of clients from type hints and drives their connect / disconnect lifecycle.

## Language

**Client**:
A dependency with an async connect / disconnect lifecycle; a singleton within its container.
_Avoid_: Service, component, provider

**NotSingletonClient**:
A client that gets a fresh instance for every consumer that declares it.
_Avoid_: Transient, factory

**Container**:
The `Dependencies` object that resolves clients and owns their lifecycle.
_Avoid_: Registry, injector

**Resolution**:
Building a client together with its whole dependency tree, before the container connects.
_Avoid_: Instantiation, wiring

**Layer**:
A set of client instances with the same height in the dependency graph: clients with no dependencies form layer 0, every other client sits one layer above its highest dependency. All clients of one layer connect concurrently.
_Avoid_: Level, tier, depth
