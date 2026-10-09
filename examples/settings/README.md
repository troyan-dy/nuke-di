# Settings

Configuration as a client: a frozen `Settings` dataclass that reads the environment, and two clients
that take it in `__init__` like any other dependency. Use it for every project with configuration:
the environment is read once, every consumer shares one validated instance, and a test replaces it
with one line.

| File               | What it holds                                                               |
|--------------------|-----------------------------------------------------------------------------|
| `clients.py`       | `Settings`, a `@client_dataclass(frozen=True)`, and `Database` / `Cache` that depend on it |
| `main.py`          | A job that prints the settings it got and checks that everyone shares them  |
| `test_settings.py` | `Settings` replaced with `di.mock()` and `di.override()`, the environment read with `monkeypatch` |

| Variable       | Default                      |
|----------------|------------------------------|
| `DATABASE_URL` | `postgresql://localhost/app` |
| `POOL_SIZE`    | `5`, at least `1`            |
| `DEBUG`        | off; `1`, `true` or `yes` turn it on |

## Run

```console
$ cd examples
$ uv run python -m settings.main
database: connected to postgresql://localhost/app, pool of 5
cache: connected, debug=False
main: Settings(database_url='postgresql://localhost/app', pool_size=5, debug=False)
main: one Settings for everyone: True
database: disconnected
cache: disconnected
$ DATABASE_URL=postgresql://db.internal/shop POOL_SIZE=20 DEBUG=1 uv run python -m settings.main
database: connected to postgresql://db.internal/shop, pool of 20
cache: connected, debug=True
main: Settings(database_url='postgresql://db.internal/shop', pool_size=20, debug=True)
main: one Settings for everyone: True
database: disconnected
cache: disconnected
```

A bad value fails the run while the tree is built, before any client connects:

```console
$ POOL_SIZE=0 uv run python -m settings.main 2>/dev/null
$ echo $?
1
$ POOL_SIZE=0 uv run python -m settings.main 2>&1 | head -n 1
Settings.__init__ raised ValueError (resolving main -> Settings): POOL_SIZE must be at least 1, got 0
```

## Test

```console
$ uv run pytest -q settings
....                                                                     [100%]
4 passed in 0.03s
```

## What to look at

- Why a client and not a module-level `settings = Settings()`: a global is read on import, so a test
  has to patch the environment before importing anything; a client is built when the container
  resolves it, once per container, and every consumer gets the same instance.
- `field(default_factory=...)` reads each variable when `Settings()` is built. The fields have
  defaults, so the container leaves them alone, and a test passes only what it cares about:
  `Settings(database_url="sqlite://", pool_size=1)`.
- `di.mock(Settings, Settings(...))` and `with di.override(Settings, Settings(...))` hand the test
  instance to `Database`, `Cache` and the job alike. `Settings` has no `connect()`, so a Replacement,
  which is never connected, loses nothing.
- `__post_init__` validates: a `ValueError` there becomes an `InitializeDependencyError` and exit
  code `1` before anything connects. `frozen=True` keeps the configuration read-only at runtime.
- `class Settings(Client)` next to `@client_dataclass` tells mypy and pyright that it is a client.
