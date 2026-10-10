# aiogram

A Telegram bot on aiogram 3: `setup(dp)` connects the clients before the dispatcher's startup handlers and
disconnects them after its shutdown handlers, and the handlers of a router take a client by type hint next
to the message. A fake Telegram in the process runs the real polling loop with no token and no network.

| File               | What it holds                                                                       |
|--------------------|-------------------------------------------------------------------------------------|
| `clients.py`       | `Database` of visits and `UserService` that depends on it                            |
| `bot.py`           | The router with `/start` and `/stats`, the dispatcher, `setup(dp)`, a startup handler |
| `fake_telegram.py` | A Bot API session that hands out updates and prints the replies, and `message()`     |
| `demo.py`          | `start_polling()` of the bot against the fake Telegram                               |
| `test_app.py`      | `feed_update()` with `Database` replaced through `DI.override()`, and the polling    |

## Run

Without a token, against the fake Telegram:

```console
$ cd examples
$ uv run python -m aiogram_bot.demo
database: connected
INFO nuke_di.core: Connected 2 clients in 0.00s (slowest: Database 0.00s, UserService 0.00s)
startup: bot 42, 0 users so far
INFO aiogram.dispatcher: Start polling
INFO aiogram.dispatcher: Run polling for bot @demo_bot id=42 - 'Demo'
bot -> chat 7: Hello, Ada! This is your visit 1.
INFO aiogram.event: Update id=1 is handled. Duration 0 ms by bot id=42
bot -> chat 7: Hello, Ada! This is your visit 2.
INFO aiogram.event: Update id=2 is handled. Duration 0 ms by bot id=42
bot -> chat 8: Hello, Grace! This is your visit 1.
INFO aiogram.event: Update id=3 is handled. Duration 0 ms by bot id=42
bot -> chat 8: 2 users so far
INFO aiogram.event: Update id=4 is handled. Duration 0 ms by bot id=42
INFO aiogram.dispatcher: Polling stopped for bot @demo_bot id=42 - 'Demo'
INFO aiogram.dispatcher: Polling stopped
database: disconnected, 2 users
```

With a token from [@BotFather](https://t.me/BotFather), the same bot polls Telegram until Ctrl+C:

```console
$ BOT_TOKEN=123456:ABC... uv run python -m aiogram_bot.bot
```

## Test

```console
$ uv run pytest -q aiogram_bot
..                                                                       [100%]
2 passed in 1.23s
```

## What to look at

- `setup(dp)` is the whole setup, called on the dispatcher: the handlers of `router`, included before it
  or after, take `users: UserService` next to `message: Message`, and nothing marks them.
- `announce`, a startup handler, takes `users: UserService` too: the clients connect before the startup
  handlers of the dispatcher and of every router, and disconnect after their shutdown handlers.
- The test replaces `Database` with `DI.override()` before `dp.emit_startup()`, which is where the clients
  are resolved, and feeds updates with `dp.feed_update()`: no polling, no Telegram.
- `FakeTelegram` is an aiogram `BaseSession`: a `Bot` made with it sends its requests there instead of
  Telegram, so the demo runs `start_polling()` as it is. The demo passes `handle_as_tasks=False` so that the
  polling stops after the last reply; a real bot keeps the default.
