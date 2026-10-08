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

**Client timing**:
How long one client's `connect()` and `disconnect()` took in the last connect of its container, and how each ended: ok, failed, timed out or cancelled. The container only measures; exporting timings is left to hooks.
_Avoid_: Metric, stat, span

**Replacement**:
An object registered in the container in place of a client class, before that class is resolved; every consumer receives it instead of the client. A Replacement is never connected. Registered with `mock()` it lasts until the next flush; registered with `override()` it lasts until the end of the `with` block.
_Avoid_: Fake, stub, double (for the concept; `mock()` is only the method name)

**Override**:
A `with` block of `override()` that registers a Replacement for its duration and leaves the container flushed.
_Avoid_: Patch

### Running

**Entrypoint**:
An async function that a process runs as its main program, declared as a Job or a Worker.
_Avoid_: Command, main, executable

**Job**:
An entrypoint that runs once: it finishes when its function returns. How often it runs is decided by an external scheduler.
_Avoid_: Cron, task, script

**Parameter**:
A command-line option of an entrypoint, declared as an annotated argument of its function that is not a client.
_Avoid_: Argument, CLI arg

**Worker**:
An entrypoint that runs until the process is asked to stop.
_Avoid_: Daemon, consumer

**Run**:
One execution of an entrypoint in a process, from resolving its clients to the exit code.
_Avoid_: Execution, launch, invocation

**Shutdown**:
The request for a Run to stop, raised by a termination signal. An entrypoint gets a grace period to finish on its own before it is cancelled.
_Avoid_: Stop token, termination, kill

**Background task**:
A coroutine started through the container-owned task supervisor rather than awaited by its caller. It is cancelled and awaited before the clients it uses disconnect.
_Avoid_: Fire-and-forget, job
