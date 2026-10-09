# Litestar

The same service in Litestar: `ClientPlugin()` fills the clients of handlers, of a `Controller` and of
a dependency declared with `Provide`, and connects them for the life of the app.

| File          | What it holds                                                                   |
|---------------|---------------------------------------------------------------------------------|
| `clients.py`  | `Database` and `UserService` that depends on it                                 |
| `app.py`      | `UserController`, the `current_user` dependency, the `/me` handler, the plugin  |
| `test_app.py` | `litestar.testing.TestClient` with `Database` replaced through `DI.override()`  |

## Run

```console
$ cd examples
$ uv run --with uvicorn uvicorn litestar_app.app:app
INFO:     Started server process [66825]
INFO:     Waiting for application startup.
database: connected
INFO - 2026-10-09 19:32:28,744 - nuke_di.core - core - Connected 2 clients in 2 layers in 0.00s (slowest: Database 0.00s, UserService 0.00s)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:53281 - "GET /users HTTP/1.1" 200 OK
INFO:     127.0.0.1:53283 - "GET /users/2 HTTP/1.1" 200 OK
INFO:     127.0.0.1:53286 - "GET /users/42 HTTP/1.1" 404 Not Found
INFO:     127.0.0.1:53288 - "GET /me HTTP/1.1" 200 OK
INFO:     127.0.0.1:53290 - "GET /me HTTP/1.1" 401 Unauthorized
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [66825]
```

In another terminal:

```console
$ curl localhost:8000/users
["alice","bob","carol"]
$ curl localhost:8000/users/2
Hello, bob!
$ curl localhost:8000/users/42
{"status_code":404,"detail":"user 42 not found"}
$ curl localhost:8000/me -H "X-User-Id: 3"
You are carol
$ curl localhost:8000/me -H "X-User-Id: 9"
{"status_code":401,"detail":"unknown X-User-Id"}
```

The `INFO - ... nuke_di.core` line is the startup summary of `nuke-di`: Litestar configures logging, so
it shows up.

## Test

```console
$ uv run pytest -q litestar_app
..                                                                       [100%]
2 passed in 0.24s
```

## What to look at

- `plugins=[ClientPlugin()]` is the whole setup: the controller methods take `db: Database` and
  `users: UserService` next to `self`, and `current_user` takes `db: Database` next to a header.
- Litestar provides dependencies by argument name, so a name means one client in the whole app:
  `db` is `Database` in every handler and dependency.
- The `/me` handler gets `user` from `Provide(current_user)`, Litestar's own mechanism; only the client
  argument of `current_user` comes from nuke-di.
- The tests replace `Database` with `DI.override()` before `TestClient` starts the app.
