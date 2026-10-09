# Startup failures

What a process does when a dependency is down: a `connect()` that raises, a `connect()` that
hangs until `CONNECT_TIMEOUT_SECONDS`, the same failure in a `@job`, and a tree that cannot be
built at all. Every case fails fast: the clients that connected are disconnected again, and a job
exits with code `1`. Retrying is left to whatever restarts the process.

| File                 | What it holds                                                                    |
|----------------------|----------------------------------------------------------------------------------|
| `clients.py`         | `Postgres` that connects, `Kafka` whose `connect()` raises, `Search` that hangs   |
| `connect_error.py`   | Connects `Orders(pg, kafka)` and catches the `ConnectError`                       |
| `connect_timeout.py` | Connects `Reports(pg, search)` and catches the `ConnectTimeoutError`              |
| `job.py`             | A `@job` that needs `Orders`: exit code `1`                                       |
| `resolution.py`      | Three clients that cannot be built: a non-client argument, no type hint, a cycle |
| `broken_job.py`      | A `@job` whose tree cannot be built: exit code `1`, nothing connects              |
| `test_startup.py`    | `pytest.raises(ConnectError)` and friends                                         |

## Run

The tracebacks are shortened to `...` below; the rest is verbatim. The log goes to stderr.

A `connect()` that raises: `Postgres` had connected, so it is disconnected again, and
`deps.timings` tells which client failed:

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
Postgres layer 0  connect ok  disconnect ok
Kafka    layer 0  connect failed  disconnect None
Orders   layer 1  connect None  disconnect None
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
Postgres layer 0  connect ok  disconnect ok
Search   layer 0  connect timed_out  disconnect None
Reports  layer 1  connect None  disconnect None
```

The same failure in a job: the body never runs, and the exit code tells the scheduler:

```console
$ uv run python -m startup_failures.job
INFO  nuke_di.run: Starting job startup_failures.job.publish
postgres: connected
ERROR nuke_di.core: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
postgres: disconnected
ERROR nuke_di.run: Run startup_failures.job.publish failed
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ...
nuke_di.errors.ConnectError: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
INFO  nuke_di.run: Run startup_failures.job.publish finished with exit code 1 in 0.103s
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
INFO  nuke_di.run: Starting job startup_failures.broken_job.checkout
ERROR nuke_di.run: Run startup_failures.broken_job.checkout failed
Traceback (most recent call last):
  ...
nuke_di.errors.InvalidSignatureError: Argument "url" of "Cache.__init__" is str, which is not a client (resolving checkout -> Checkout -> Cache)
INFO  nuke_di.run: Run startup_failures.broken_job.checkout finished with exit code 1 in 0.001s
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
- The clients that connected are rolled back, layers in reverse; a client that failed, and the
  layers above it, are never disconnected because they never connected.
- `ConnectError` derives from `SystemExit`: a process that does not catch it stops. A dependency
  that is down is reported, not retried: the orchestrator restarts the process.
- A resolution error is a bug in the code, not in the environment, and the test of a job's wiring
  (`Dependencies().inject(publish)`) catches it in CI without any database.
