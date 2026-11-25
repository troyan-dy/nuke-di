# Startup failures

What a process does when a dependency is down: a `connect()` that raises, a `connect()` that
hangs until `CONNECT_TIMEOUT_SECONDS`, the same failure in a `@entrypoint`, and a tree that cannot be
built at all. Every case fails fast: the clients that connected are disconnected again, and a job
exits with code `1`. Retrying is left to whatever restarts the process.

| File                 | What it holds                                                                    |
|----------------------|----------------------------------------------------------------------------------|
| `clients.py`         | `Postgres` that connects, `Kafka` whose `connect()` raises, `Search` that hangs   |
| `connect_error.py`   | Connects `Orders(pg, kafka)` and catches the `ConnectError`                       |
| `connect_timeout.py` | Connects `Reports(pg, search)` and catches the `ConnectTimeoutError`              |
| `job.py`             | A `@entrypoint` that needs `Orders`: exit code `1`                                       |
| `resolution.py`      | Three clients that cannot be built: a non-client argument, no type hint, a cycle |
| `broken_job.py`      | A `@entrypoint` whose tree cannot be built: exit code `1`, nothing connects              |
| `test_startup.py`    | `pytest.raises(ConnectError)` and friends                                         |

## Run

The tracebacks are shortened to `...` below; the rest is verbatim. The log goes to stderr.

A `connect()` that raises triggers rollback. The log names the failing client and shows its error.

```console
$ cd examples
$ uv run python -m startup_failures.connect_error
postgres: connected
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
postgres: disconnected
ConnectError: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
__cause__: OSError('broker kafka-1:9092 is unreachable')
connected: False
```

A `connect()` that never returns, cut off after one second instead of the default 30:

```console
$ CONNECT_TIMEOUT_SECONDS=1 uv run python -m startup_failures.connect_timeout
postgres: connected
Search did not connect within 1s (CONNECT_TIMEOUT_SECONDS)
Traceback (most recent call last):
  ...
asyncio.exceptions.CancelledError

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ...
TimeoutError
postgres: disconnected
ConnectTimeoutError: Search did not connect within 1s (CONNECT_TIMEOUT_SECONDS)
__cause__: TimeoutError()
```

The same failure in a job: the body never runs, and the exit code tells the scheduler:

```console
$ uv run python -m startup_failures.job
postgres: connected
ERROR nuke_di: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
postgres: disconnected
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ...
nuke_di.errors.ConnectError: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
$ echo $?
1
```

A tree that cannot be built fails in `resolve()`, before `connect()` is reached:

```console
$ uv run python -m startup_failures.resolution
InvalidSignatureError: Argument "url" of "Cache.__init__" is str, which is not a client (resolving Checkout -> Cache)
InvalidSignatureError: Argument "pg" of "Audit.__init__" has no type hint (resolving Audit)
CircularDependencyError: Circular dependency: Orders -> Payments -> Orders
```

In a job it is exit code `1` too, and `postgres: connected` never appears:

```console
$ uv run python -m startup_failures.broken_job
Traceback (most recent call last):
  ...
nuke_di.errors.InvalidSignatureError: Argument "url" of "Cache.__init__" is str, which is not a client (resolving checkout -> Checkout -> Cache)
$ echo $?
1
```

## Test

```console
$ uv run pytest -q startup_failures
......                                                                   [100%]
6 passed in 0.24s
```

## What to look at

- `ConnectError` carries the original exception as `__cause__`; `ConnectTimeoutError` is a
  `ConnectError`, so one `except ConnectError` covers both.
- The clients that connected are rolled back, each after the clients that depend on it; a client
  that failed, and the clients that need it, are never disconnected because they never connected.
- A process with an unhandled `ConnectError` exits with a traceback. An orchestrator can restart the
  process under its own retry policy.
- A resolution error is a bug in the code, not in the environment, and the test of a job's wiring
  (`Dependencies().inject(publish)`) catches it in CI without any database.
