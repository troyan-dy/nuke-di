# taskiq integration

Status: implemented.
Issue: [#69](https://github.com/troyan-dy/nuke-di/issues/69), a follow-up of [#12](https://github.com/troyan-dy/nuke-di/issues/12).
Decision record: [ADR-0003](../adr/0003-fastapi-signature-rewrite.md), whose signature rewrite this integration shares.
Terms: see [CONTEXT.md](../../CONTEXT.md). This spec uses **Client**, **Container**, **Resolution**, **Replacement** and **Override** as defined there.

## Problem

A taskiq worker has to connect its clients in a `WORKER_STARTUP` handler by hand, keep them in `TaskiqState` and reach them through `Context` or a dependency per client. A task that declares `users: UserService` gets taskiq expecting `users` in the message.

## Goal

```python
from taskiq import InMemoryBroker

from nuke_di.taskiq import setup

broker = InMemoryBroker()
setup(broker)


@broker.task
async def send_report(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))
```

`taskiq worker app.tasks:broker` connects the clients when the worker starts and disconnects them when it stops. A process that only kicks tasks (`.kiq()`) connects nothing.

## Non-goals

- **Per-task clients**: a `Client` is one per container and a `NotSingletonClient` one per argument (ADR-0006). taskiq's own generator dependencies stay available for what lives as long as one task.
- **Clients of a dependency class**: taskiq builds a class dependency from its own `__init__`, which `bind()` does not rewrite; such a class is a client, or takes clients through a dependency function.
- **The scheduler process** (`taskiq scheduler`): it fires no worker events and runs no tasks.
- **A container shared with another integration in one process**: one container connects once. An `InMemoryBroker` started inside a FastAPI app on the same container fails to start with "the container is already connected".

## Public API

Module `nuke_di.taskiq`, installed with the `taskiq` extra (`pip install nuke-di[taskiq]`: `taskiq>=0.11`, `taskiq-dependencies>=1.5`). `import nuke_di` never imports taskiq.

### `setup(broker, container=DI)`

`broker` is any `AsyncBroker`.

1. Rewrites the signature of every task the broker has (`broker.get_all_tasks()`, shared tasks included) as in FastAPI (ADR-0003), with `TaskiqDepends`, and of the `TaskiqDepends` functions they use, at any depth.
2. Replaces `broker.task` with a wrapper that rewrites every task declared later; `broker.register_task()` goes through `broker.task`.
3. Inserts a `WORKER_STARTUP` handler first in `broker.event_handlers`: it collects the bindings of `broker.get_all_tasks()` and enters `running()`, which resolves the clients and connects the container. A failed connect is flushed and raised as a `RuntimeError` out of `broker.startup()`, which fails the worker.
4. Replaces `broker.shutdown` with a wrapper that runs the broker's own shutdown (the `WORKER_SHUTDOWN` handlers, the middlewares, the result backend) and then leaves `running()`: `Shutdown`, `BackgroundTasks`, `disconnect()`, even when the broker's shutdown raised.
5. Raises `TypeError` when called twice for the same broker.

Which process connects is taskiq's decision: `broker.startup()` fires `WORKER_STARTUP` in a worker process (`is_worker_process`, set by `taskiq worker`) and `CLIENT_STARTUP` elsewhere; an `InMemoryBroker` fires both, since it runs the tasks it is kicked. A task run without the worker's startup, e.g. on an `InMemoryBroker` that was never started, fails with "is not connected"; a task declared after the worker started, with "was not started with the worker".

A function is rewritten once, whatever the container (`per_container=False`): a worker reads a task's signature when it builds its `Receiver`, and an `InMemoryBroker` on the task's first run, which may come after another broker has started.

### Internals read

`broker.get_all_tasks()`, `task.original_func`, `broker.task`, `broker.event_handlers`, `TaskiqEvents` and `broker.shutdown` are public. The integration relies on how taskiq reads a task: `Receiver.__init__` (and `run_task()` for a task it has not seen) calls `inspect.signature()` and builds `taskiq_dependencies.DependencyGraph(handler)`, which takes a `Dependency` out of `Annotated[...]` metadata and honours `__signature__`. `taskiq worker` imports the broker, sets `is_worker_process`, imports the task modules and builds the `Receiver` before `receiver.listen()` awaits `broker.startup()`, so the rewrite must happen when a task is registered, not on startup. An `InMemoryBroker` builds its `Receiver` in its own `__init__`, which reads the shared tasks there are at that moment. CI runs the taskiq tests on taskiq 0.11.0 with taskiq-dependencies 1.5.0, and on the latest releases.

## Typing

taskiq types `task.kiq()` with the task's own signature, so a type checker asks for the client argument in `send_report.kiq(42)`. A default, `users: UserService = TaskiqDepends()`, makes the argument optional for the checker; the rewrite still fills it from the container. This is taskiq's own idiom for its dependencies, and the same holds for any argument taskiq fills.

## Testing

```python
with DI.override(Database, replacement):
    await broker.startup()
    task = await send_report.kiq(1)
    await task.wait_result()
    await broker.shutdown()
```

The test broker is an `InMemoryBroker`, in place of the real one. The task stays a plain function, callable directly with its clients.

## Decisions from self-grilling

| # | Question | Decision |
|---|---|---|
| 1 | Mechanism | The FastAPI signature rewrite (ADR-0003), shared in `nuke_di.integration`: taskiq-dependencies reads `inspect.signature()` and the `Annotated` metadata, so `Annotated[C, TaskiqDepends(getter)]` works, nested dependency functions included. |
| 2 | When the signature is rewritten | When a task is registered, through `broker.task`: the worker's `Receiver` builds the dependency graphs before the startup event, so a rewrite on startup would come too late. |
| 3 | Connect: an event or a wrapper | The `WORKER_STARTUP` event, inserted first: taskiq decides which process is a worker, and each broker class fires the events its own way (an `InMemoryBroker` fires both). Wrapping `startup()` would have to guess the worker side. |
| 4 | Disconnect: an event or a wrapper | A wrapper of `broker.shutdown`: a `WORKER_SHUTDOWN` handler runs in registration order, so the handlers registered after `setup()`, the middlewares and the result backend would see disconnected clients. |
| 5 | Which tasks | `broker.get_all_tasks()`: the broker's own tasks and the shared ones, which a worker of any broker serves. A task of another broker starts nothing. |
| 6 | The client process | Connects nothing: `CLIENT_STARTUP` is not hooked. The web app that kicks tasks connects its own clients through its own integration, on the same container or another. |
| 7 | `.kiq()` and type checkers | Documented, not worked around: a default `= TaskiqDepends()` on the client argument. A mypy plugin hook could drop client arguments from `kiq`, but pyright has no plugins. |
| 8 | Minimum taskiq | 0.11.0, with taskiq-dependencies 1.5.0: on 1.4 a task's own `Annotated[..., TaskiqDepends(f)]` written under `from __future__ import annotations` is not found, rewritten or not. `async with broker` came after 0.12, so the tests start the broker with `startup()` / `shutdown()`. |
