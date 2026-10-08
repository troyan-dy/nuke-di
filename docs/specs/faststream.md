# FastStream integration

Status: implemented.
Issue: [#19](https://github.com/troyan-dy/nuke-di/issues/19), a follow-up of [#12](https://github.com/troyan-dy/nuke-di/issues/12).
Decision record: [ADR-0003](../adr/0003-fastapi-signature-rewrite.md), whose signature rewrite this integration shares.
Terms: see [CONTEXT.md](../../CONTEXT.md). This spec uses **Client**, **Container**, **Resolution**, **Replacement** and **Override** as defined there.

## Problem

A service on FastStream has to connect its clients in `on_startup` by hand and reach them through globals or `Context()`. A subscriber that declares `users: UserService` gets FastStream reading `users` from the message instead.

## Goal

```python
from faststream import FastStream
from faststream.nats import NatsBroker

from nuke_di.faststream import setup

broker = NatsBroker()
app = FastStream(broker)
setup(app)


@broker.subscriber("greetings")
async def greet(user_id: int, users: UserService) -> None:
    print(await users.greet(user_id))
```

The clients connect before the broker starts consuming and disconnect after it stops.

## Non-goals

- **A broker run without a FastStream app**, e.g. a broker started inside a FastAPI lifespan or FastStream's own FastAPI router: `setup()` takes the app.
- **Clients in `on_startup` / `lifespan` hooks by type hint**: those run before or around the brokers; they reach clients through the container.
- **Per-message clients**: as everywhere, a `Client` is one per container and a `NotSingletonClient` one per argument.

## Public API

Module `nuke_di.faststream`, installed with the `faststream` extra (`pip install nuke-di[faststream]`, `faststream>=0.6`). `import nuke_di` never imports FastStream.

### `setup(app, container=DI)`

`app` is a `FastStream` or an `AsgiFastStream`.

1. Wraps `app.lifespan_context`, which FastStream enters before `on_startup`, before the brokers start, and leaves after the brokers stop and after `after_shutdown`. On entry: refuse a container that is already connected; for every broker of the app, every subscriber of `broker.subscribers` (routers included), take the function it was declared with and the dependencies of the subscriber, of its routers and of the broker; rewrite their signatures as in FastAPI (ADR-0003), with `faststream.Depends`; resolve the clients; connect the container; then enter the app's own lifespan. On exit, after the app's own lifespan: `Shutdown`, `BackgroundTasks`, `disconnect()`. A failed startup is rolled back, flushed, and raised as a `RuntimeError`.
2. Adds a decorator to `fd_config.call_decorators` of every broker of the app, which FastStream applies to a subscriber's declared function every time it builds the subscriber, i.e. on every broker start. It rewrites the signature too, so a broker started without the app (e.g. `TestNatsBroker(broker)` without `TestApp`) fails with "not connected" instead of reading the client from the message.
3. Raises `TypeError` when called twice for the same app.

A function is rewritten once, whatever the container, and every startup resolves the same bindings from the container of the app that starts: FastStream 0.6 under a test broker builds a subscriber before the app's lifespan runs, so the signature must not change from one app to the next. Apps that share a subscriber function, e.g. an app per test on a module-level broker, therefore run one at a time. The decorator is installed once per broker and holds the container of the latest app set up on it, which only names the client in errors.

Subscribers may be declared before or after `setup()`. A subscriber added after the app started is rewritten when it starts but its clients were never resolved: its getter raises "was not started with the app".

### Internals read

`broker.subscribers` and `subscriber.calls` are public. The declared function is `handler._declared_call` (FastStream 0.7) or `handler._original_call` (0.6), and the dependencies of the broker and routers are `subscriber._outer_config.broker_dependencies`. Both are private; CI runs the FastStream tests on 0.6.0 and on the latest release.

## Testing

```python
with DI.override(Database, replacement):
    async with TestNatsBroker(broker) as test_broker, TestApp(app):
        await test_broker.publish(1, "greetings")
```

`TestApp` runs the lifespan; a test broker alone does not. The handler stays a plain function, callable directly with a Replacement.

## Decisions from self-grilling

| # | Question | Decision |
|---|---|---|
| 1 | Mechanism | The FastAPI signature rewrite (ADR-0003), shared in `nuke_di._integration`: fast-depends reads `inspect.signature()`, so `Annotated[C, Depends(getter)]` works, nested `Depends` functions and classes included. |
| 2 | Hook for connecting | `app.lifespan_context`, wrapped: the outermost scope, entered by `faststream run`, `TestApp` and the ASGI app alike, so the app's lifespan and every hook see connected clients and the brokers consume only after the clients connect. `on_startup` / `after_shutdown` hooks would run in registration order relative to the user's. |
| 3 | When the clients are found | On startup, from the brokers' subscribers, not when a subscriber is declared: FastStream has no registration hook, and routers are declared apart from the broker. |
| 4 | Why also `call_decorators` | The field FastStream keeps "to patch injection by integrations" (its own comment), applied on every build: without it a test that forgets `TestApp` gets a pydantic error about the message instead of the fix. |
| 5 | Which subscribers | Those of the app's brokers, routers included. A subscriber of another broker starts nothing. |
| 6 | Minimum FastStream | 0.6.0, where `broker.subscribers`, `fd_config.call_decorators` and `lifespan_context` look as in 0.7; 0.5 is a different design. CI runs the FastStream tests on it with NATS. |
| 8 | An app per test on one module-level broker (found in review) | Bindings are made once per function and resolved by whichever app starts; one decorator per broker. A binding per container broke it: the decorator of an earlier app rebound the function to its own container on every broker start. |
| 7 | Test broker in nuke-di's own tests | NATS (`faststream[nats]`): a pure-Python client, so CI needs no service. |
