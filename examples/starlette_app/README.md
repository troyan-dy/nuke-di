# Starlette

Starlette has no dependency injection, so its handlers take clients from `nuke_di.asgi.lifespan()`: the
app's lifespan connects the clients it lists on startup and disconnects them on shutdown, and a handler
asks it for one with `clients.get(UserService)`. Quart, aiohttp and a plain ASGI app use the same object,
see [ASGI](../../docs/guide/asgi.md).

| File          | What it holds                                                                        |
|---------------|--------------------------------------------------------------------------------------|
| `clients.py`  | `Database` and `UserService` that depends on it                                      |
| `app.py`      | `clients = lifespan(DI, UserService, Database)`, two handlers and `lifespan=clients` |
| `test_app.py` | Starlette's `TestClient` with `Database` replaced through `DI.override()`            |

## Run

```console
$ cd examples
$ uv run --with uvicorn uvicorn starlette_app.app:app
INFO:     Started server process [45493]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:49986 - "GET /users/1 HTTP/1.1" 200 OK
INFO:     127.0.0.1:49988 - "GET /users/42 HTTP/1.1" 404 Not Found
INFO:     127.0.0.1:49990 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [45493]
```

In another terminal:

```console
$ curl localhost:8000/users/1
Hello, alice!
$ curl localhost:8000/users/42
user 42 not found
$ curl localhost:8000/me -H "X-User-Id: 2"
You are bob
```

## Test

```console
$ uv run pytest -q starlette_app
....                                                                     [100%]
4 passed in 0.07s
```

## What to look at

- `lifespan(DI, UserService, Database)` lists the clients the handlers take; `Database` is listed
  because `me` takes it, not because `UserService` depends on it. A handler that asks for a client
  the list lacks gets a `RuntimeError` that says to list it.
- Importing `app.py` resolves nothing: the clients are resolved on **every startup**, so
  `DI.override()` before `TestClient` starts the app replaces `Database`, and a second start (the next
  test) gets fresh, connected clients. `test_restarts_with_fresh_clients` checks the second.
- Without the lifespan, `TestClient(app).get(...)` outside `with`, a handler raises
  `RuntimeError: UserService is not connected: start the app with its lifespan`, followed by how;
  `test_without_the_lifespan` shows it.
