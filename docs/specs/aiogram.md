# aiogram integration

Status: implemented.
Issue: [#70](https://github.com/troyan-dy/nuke-di/issues/70), part of the integrations epic [#105](https://github.com/troyan-dy/nuke-di/issues/105).
Decision records: [ADR-0004](../adr/0004-litestar-clients-by-name.md) gives Litestar's clients by name too, but its rule that one name means one client in the whole app does not hold here: aiogram's names are per handler (decisions 2 and 10). [ADR-0006](../adr/0006-clients-live-as-long-as-the-container.md): why there is no client per update.
Terms: see [CONTEXT.md](../../CONTEXT.md). This spec uses **Client**, **Container**, **Resolution**, **Replacement**, **Override**, **Shutdown** and **Background task** as defined there.

## Problem

aiogram 3 passes a handler the items of its middleware data by name, and only those the handler declares. It has no injection by type, so a bot keeps its clients in globals or in `dp["db"] = ...`, connects them in a startup handler by hand, and a test replaces them by assigning to the dispatcher. dishka's integration needs `@inject` and `FromDishka[T]` on every handler.

## Goal

```python
from aiogram import Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from nuke_di.aiogram import setup

router = Router()


@router.message(CommandStart())
async def start(message: Message, users: UserService) -> None:
    await message.answer(await users.greet(message.chat.id))


dp = Dispatcher()
dp.include_router(router)
setup(dp)
```

The clients connect, each after its own dependencies, before the dispatcher's startup handlers, and disconnect after its shutdown handlers. `override()` before `start_polling()` or `dp.emit_startup()` replaces them.

## Non-goals

- **A client per update** (the issue's "scoped client" around `await handler(event, data)`): rejected, ADR-0006. Per-update state is opened through a method of a long-lived client.
- **Clients in filters**: aiogram runs a handler's filters, and those of its observer (`router.message.filter(...)`), before its inner middlewares, the first place where the handler is known. A filter with a client argument raises `TypeError` on startup.
- **Clients in scenes** (`aiogram.fsm.scene`): refused with `TypeError` on startup, decision 13.
- **Class-based handlers** (`MessageHandler` and the other `BaseHandler` subclasses): aiogram owns their `__init__` and passes the data as `self.data`; they get no clients, and are left alone.
- **`setup()` on a `Router`**: refused with `TypeError`, see decision 3.

## Public API

Module `nuke_di.aiogram`, installed with the `aiogram` extra (`pip install nuke-di[aiogram]`, `aiogram>=3.2`). `import nuke_di` never imports aiogram.

### `setup(dp, container=DI)`

`dp` is a `Dispatcher`.

1. Registers one inner middleware on every observer of `dp.observers` (`message`, `callback_query`, ..., `update`, `error`). aiogram runs the inner middlewares of an observer of every router on the way from the dispatcher to the router of the handler (`TelegramEventObserver._resolve_middlewares` walks `router.chain_head`), so one middleware per event type covers every router included into the dispatcher, before `setup()` or after. The middleware reads `data["handler"].callback`, which aiogram sets before it runs the inner middlewares, and looks up the client arguments of that callback in a dictionary keyed by its `id()`, whose entry keeps the callback (a callable object may be unhashable). For a handler with clients it calls the next step with a copy of the data plus `data[name]` for the client of each, so the next handler after a `SkipHandler` gets the data without them; a name already in the data, from a filter's result, a middleware or `feed_update(**kwargs)`, raises `TypeError` instead of being replaced. aiogram then passes the handler the arguments it declares.
2. Replaces `dp.emit_startup` and `dp.emit_shutdown` on the instance; `start_polling()`, the aiohttp webhook app (`setup_application`) and a test all call these.
   - Startup: walks `dp.chain_tail` (the dispatcher and every router below it), every observer's handlers, and the startup and shutdown handlers; collects the client arguments of each callback, one `Binding` per client class; refuses filters with clients (of a handler, and of an observer: `observer._handler.filters`), scenes with clients, a client argument under a name aiogram passes itself, and one name for two clients among the startup and shutdown handlers. Then `running()` resolves the clients and connects the container, and the original `emit_startup` runs with the client arguments of the startup handlers added to its keyword arguments. A startup handler that raises disconnects the container before the error propagates, since `start_polling()` calls no shutdown after a failed startup. The bindings found replace the previous ones only once the container connected, so a second startup of a running dispatcher fails and leaves the first one's clients in place.
   - Shutdown: the original `emit_shutdown`, with the client arguments of the shutdown handlers, then, also when it raises, the end of `running()`: `Shutdown`, `BackgroundTasks`, `disconnect()`. A keyword argument of the call under one of those names raises `TypeError`. When the dispatcher is not started, after a failed startup (the aiohttp webhook app calls the shutdown on cleanup anyway) or on a second shutdown, the original `emit_shutdown` runs with the arguments of the call alone.
3. Raises `TypeError` when called twice for the same dispatcher, or with something that is not a `Dispatcher`.

Reserved names, refused on startup: `bot`, `bots`, `dispatcher`, `router`, `event_router`, `event_update`, `handler`, `event_context`, `event_from_user`, `event_chat`, `event_thread_id`, `event_business_connection_id`, `fsm_storage`, `state`, `raw_state`, `scenes`, `callback_answer`, every key of `dp.workflow_data`, and every keyword argument of the startup (those of `start_polling()`).

A handler first seen by the middleware, because it was registered after the startup or an update came before it, gets its client arguments looked up then; a client resolved on startup for another handler, or one the container connected as a dependency of another client, fills it, and one nobody asked for raises `RuntimeError("<Client> was not started with the dispatcher: ...")`. Before the startup or after the shutdown every client raises `RuntimeError("<Client> is not connected: start the dispatcher with `start_polling()`, or `await dp.emit_startup()` in a test")`.

### Internals read

The client arguments are read as aiogram reads a signature: through `functools.wraps`, without the arguments a `functools.partial` binds, from `__call__` for a callable object, and on Python 3.14 with `annotationlib.Format.FORWARDREF`. When the type hints of a function do not evaluate, whatever the error, each is evaluated alone and only the broken ones are left out.

`Dispatcher.observers`, `TelegramEventObserver.handlers` / `.middleware`, `EventObserver.handlers`, `HandlerObject.callback` / `.filters`, `FilterObject.callback`, `Router.chain_tail`, `Dispatcher.workflow_data`, `emit_startup` / `emit_shutdown` and `data["handler"]` are public attributes, the same from 3.0 to 3.31 (checked in the source of both). Two are private: `TelegramEventObserver._handler.filters`, where `observer.filter(...)` keeps the filters of an observer, and, for the scenes, `SceneHandlerWrapper.scene` with `Scene.__scene_config__.handlers` / `.actions`; both look the same in 3.2, 3.4 and 3.31. CI runs the aiogram tests on 3.2.0, the lowest release that installs next to the test dependencies (3.0 and 3.1 pin a `typing-extensions` older than pytest-asyncio needs), and on the latest.

## Testing

aiogram has no test client. A test makes a `Bot` with a session of its own, a subclass of `aiogram.client.session.base.BaseSession` that records the requests, and drives the dispatcher:

```python
with DI.override(Database, replacement):
    await dp.emit_startup(bot=bot)
    try:
        await dp.feed_update(bot, update)
    finally:
        await dp.emit_shutdown(bot=bot)
```

`feed_update()` raises what the handler raised unless an error handler handles it. The contract of `nuke_di.integration.testing.check()` runs this way, with the check's handler wrapped by `functools.wraps` in one that takes the message first. A session that also answers `getMe` and `getUpdates` runs `start_polling()` itself, as `examples/aiogram_bot` does.

## Decisions from self-grilling

| # | Question | Decision |
|---|---|---|
| 1 | How the client reaches the handler | By name in the middleware data, aiogram's own mechanism, as Litestar's `Provide` by name (ADR-0004). aiogram filters a handler's keyword arguments by the names of its signature, so nothing in the handler is rewritten. |
| 2 | Which middleware | Inner: an outer middleware runs before the handler is matched and would have to fill every client of every handler of the observer on every update. The inner one sees `data["handler"]`, so it fills only the handler's own clients; one name may mean different clients in different handlers, unlike ADR-0004's one name per app for Litestar. |
| 3 | `setup()` on what | The `Dispatcher` only. A router has no startup of its own: aiogram calls a router's startup and shutdown handlers from the dispatcher's. A router is included into one parent only (`Router is already attached`), so the same router in two dispatchers cannot happen, and the dispatcher's middleware covers the routers below it. |
| 4 | Hook for connecting | `emit_startup` / `emit_shutdown` wrapped, not a handler of `dp.startup` / `dp.shutdown`. aiogram calls the shutdown handlers of the routers after those of the dispatcher, so a handler of `dp.shutdown` would disconnect the clients before a router's shutdown handler that uses them; and `start_polling()` calls no shutdown when a startup handler fails, which would leave the container connected. The wrapper connects before every startup handler and disconnects after every shutdown handler and after a failed startup. |
| 5 | When the clients are found | On startup, from the handlers of the dispatcher and its routers: aiogram has no registration hook, and routers are included apart from the dispatcher, often after `setup()`. |
| 6 | Cost of an update | One dictionary lookup by the id of the handler's callback, and for a handler with clients a copy of the data with one item per client argument: under 1 µs (0.2–0.8 µs over runs) on top of aiogram's 22 µs dispatch of a message, measured with `feed_update()` on Python 3.14 against a handler that takes the same object from workflow data. |
| 7 | Filters | Refused with `TypeError` on startup, those of an observer too, rather than left to fail per update with aiogram's `missing 1 required positional argument`. Filling them would need an outer middleware with every client of the observer, decision 2. |
| 8 | Names aiogram passes itself | Refused on startup: the middleware would replace `state` or `bot` for the handler and for the middlewares after it. A name of the workflow data is refused too, so a bot that moves from `dp["db"]` to a client is told to drop one of them. |
| 9 | Startup and shutdown handlers | Take clients by type hint, through the keyword arguments of `emit_startup` / `emit_shutdown`, which aiogram passes to all of them: one name means one client among them. |
| 10 | `NotSingletonClient` | One instance per client class for the whole dispatcher, as one binding serves a class. New code does not build on it (ADR-0006). |
| 11 | Handlers still running at shutdown | Left to aiogram: `start_polling()` does not wait for the update tasks it started, and nuke-di disconnects after the shutdown handlers. Documented in the guide. |
| 12 | Minimum aiogram | 3.2, decision of "Internals read"; `make test-aiogram-min` and the `aiogram-min` CI job run the tests on it. |
| 13 | Scenes (found in review) | Refused with `TypeError` on startup when a handler or an action of a scene takes a client. Unwrapping `SceneHandlerWrapper.handler.callback` would serve the scene's message handlers, since they come through the middleware, but not its actions (`on.message.enter()` and the rest): aiogram calls those from `ScenesManager` with the data of the update that entered the scene, which the middleware's copy never reaches. Half of a scene with clients would be worse than none; a scene gets what it needs through `scenes.enter(Scene, **kwargs)`. A scene with actions alone has no handler on a router and is not seen. |
| 14 | Data under a client's name (found in review) | `TypeError` on the update, naming the key and the handler, rather than replacing a value a filter or a middleware put there on purpose. |
| 15 | Data shared with the next handler (found in review) | A copy for a handler with clients: aiogram's `trigger()` passes one dict to every handler it tries, so after a `SkipHandler` the next one would see the clients of the first. Handlers without clients get the data as it is. |
| 16 | Shutdown when not started (found in review) | The original shutdown with the caller's arguments, so the shutdown handlers without clients still run, e.g. `dp.fsm.close` on the webhook app's cleanup after a failed startup; a handler with a client fails with aiogram's missing argument then. |
| 17 | A handler first seen after the startup (found in review) | Gets a client the container connected, also one connected only as a dependency of another client, from `container.clients` while the dispatcher runs; a client the container never built raises "was not started with the dispatcher". |
| 18 | Hints that do not evaluate (found in review) | Each hint alone, whatever the error (`NameError`, `AttributeError` of `"types.NoSuchThing"`, ...), so one broken hint does not hide the other clients; aiogram itself reads a signature without evaluating it. |
