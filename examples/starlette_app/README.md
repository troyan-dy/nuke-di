# Starlette, or any framework without an integration

nuke-di ships integrations for FastAPI, Litestar and FastStream. For any other framework, plain
Starlette here, aiohttp, Sanic and the rest, the recipe is the same: bind the handlers with `inject()`
and wrap the app's lifespan in `async with DI`. `wiring.py` does both in one small class.

| File          | What it holds                                                                      |
|---------------|------------------------------------------------------------------------------------|
| `clients.py`  | `Database` and `UserService` that depends on it                                    |
| `wiring.py`   | `Wiring`: turns a handler that takes clients into an endpoint, and the lifespan    |
| `app.py`      | Two handlers that take clients, the routes and `lifespan=wiring.lifespan`           |
| `test_app.py` | Starlette's `TestClient` with `Database` replaced through `DI.override()`          |

## Run

```console
$ cd examples
$ uv run --with uvicorn uvicorn starlette_app.app:app
INFO:     Started server process [66986]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:53321 - "GET /users/1 HTTP/1.1" 200 OK
INFO:     127.0.0.1:53323 - "GET /users/42 HTTP/1.1" 404 Not Found
INFO:     127.0.0.1:53325 - "GET /me HTTP/1.1" 200 OK
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [66986]
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
...                                                                      [100%]
3 passed in 0.06s
```

## What to look at

- On import `wiring.endpoint(greet_user)` only records the handler; the framework gets a plain
  `async def call(request)`, the shape every framework accepts.
- The lifespan calls `DI.inject()` for every handler on **every startup**, then `async with DI`. Binding
  once at import looks simpler but breaks twice: `disconnect()` flushes the container, so a second start
  (the next test) would reuse clients that are no longer connected, and `DI.override()` needs a container
  with nothing resolved yet. `test_restarts_with_fresh_clients` checks the first.
- Another framework needs the same two steps in its own startup hooks: in aiohttp a `cleanup_ctx`
  generator, in Sanic `before_server_start` and `after_server_stop`. Only the request and response types
  in `wiring.py` change.
