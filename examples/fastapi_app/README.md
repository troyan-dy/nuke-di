# FastAPI

A small FastAPI service: routes, a router, a dependency and a websocket take clients by type hint,
`setup(app)` connects them on startup, and the app's own lifespan runs inside the connected container.
The user cache warms on connect and refreshes itself through `BackgroundTasks`.

| File          | What it holds                                                                       |
|---------------|-------------------------------------------------------------------------------------|
| `clients.py`  | `Database`, and `UserCache` on top of it that refreshes in a background task        |
| `api.py`      | The app: `setup(app)`, a route, a `ClientRouter`, a `Depends`, a websocket, a lifespan |
| `test_api.py` | `TestClient` with `Database` replaced through `DI.override()`, and the refresh loop  |

## Run

```console
$ cd examples
$ uv run --with uvicorn uvicorn fastapi_app.api:app
INFO:     Started server process [66095]
INFO:     Waiting for application startup.
database: connected
cache: warmed with 3 users
app: started with Database, BackgroundTasks, UserCache
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:53211 - "GET /users/1 HTTP/1.1" 200 OK
INFO:     127.0.0.1:53213 - "GET /users/42 HTTP/1.1" 404 Not Found
INFO:     127.0.0.1:53215 - "GET /me HTTP/1.1" 200 OK
INFO:     127.0.0.1:53217 - "GET /me HTTP/1.1" 401 Unauthorized
cache: refreshed, 3 users
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
app: stopping
cache: disconnected
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [66095]
```

In another terminal:

```console
$ curl localhost:8000/users/1
"alice"
$ curl localhost:8000/users/42
{"detail":"user 42 not found"}
$ curl localhost:8000/me -H "X-User-Id: 2"
"Hello, bob!"
$ curl localhost:8000/me -H "X-User-Id: 9"
{"detail":"unknown X-User-Id"}
```

The websocket `/ws/users` is exercised in the tests: plain `uvicorn` has no websocket library, serve the
app with `--with "uvicorn[standard]"` to reach it from a browser.

## Test

```console
$ uv run pytest -q fastapi_app
....                                                                     [100%]
4 passed in 0.24s
```

## What to look at

- `setup(app)` comes before the routes; after it `get_user`, `current_user` and `lookup` declare
  `cache: UserCache` and nothing else: no `Depends` for the client, no `inject()`.
- `account = ClientRouter(prefix="/me")`: a plain `APIRouter` would not fill clients.
- The startup order: `Database` connects, then `UserCache` (the next layer) warms from it and spawns its
  refresh loop, then the app's own `lifespan` runs and already sees the connect timings. On shutdown the
  lifespan ends first, the refresh task is cancelled, and only then the clients disconnect.
- The tests replace `Database` with `DI.override()` before `TestClient` starts the app; the real one is
  never connected. `test_cache_refreshes` checks the background loop on its own, with the `di` fixture.
