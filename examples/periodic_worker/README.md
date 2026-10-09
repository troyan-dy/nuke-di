# Periodic worker

A worker that does one piece of work every `--interval` seconds, here deleting expired sessions, and stops
the moment it is asked to instead of sleeping out the rest of the interval. Use it for housekeeping that
should run every few seconds with warm connections.

| File              | What it holds                                                                |
|-------------------|------------------------------------------------------------------------------|
| `clients.py`      | `Sessions`: sessions with an expiry time, five demo sessions added on connect |
| `cleanup.py`      | The `cleanup` worker with its `--interval` parameter                         |
| `test_cleanup.py` | `delete_expired()`, the loop stopping at once on Shutdown, and a run through `di` |

In Kubernetes this is a Deployment: the process stays up and keeps its connections. A schedule of minutes
or hours, where every run is independent, is a `@job` started by a CronJob instead.

## Run

```console
$ cd examples
$ uv run python -m periodic_worker.cleanup --help
usage: python -m periodic_worker.cleanup [-h] [--interval INTERVAL]

Delete the expired sessions every --interval seconds until SIGTERM or SIGINT.

options:
  -h, --help           show this help message and exit
  --interval INTERVAL  Seconds between two cleanups (default: 5.0)
```

Every second, then SIGTERM, as Kubernetes sends it, in the middle of a wait:

```console
$ SHUTDOWN_GRACE_SECONDS=3 uv run python -m periodic_worker.cleanup --interval 1 &
sessions: connected, 5 sessions
cleanup: deleted [], 5 left
cleanup: deleted ['session-1'], 4 left
cleanup: deleted ['session-2'], 3 left
cleanup: deleted ['session-3'], 2 left
$ kill -TERM %1
cleanup: stopped
sessions: disconnected
$ wait %1; echo $?
143
```

`SHUTDOWN_GRACE_SECONDS` (10 by default) is how long a cleanup already running may go on after the
signal before it is cancelled; the wait between two cleanups ends at once whatever its value. Keep
`terminationGracePeriodSeconds` of the Deployment above it.

## Test

```console
$ uv run pytest -q periodic_worker
...                                                                      [100%]
3 passed in 0.06s
```

## What to look at

- The pause is `asyncio.wait_for(shutdown.wait(), timeout=interval)` with `TimeoutError` suppressed: it
  returns after `interval` seconds, or as soon as Shutdown is set. With `asyncio.sleep(interval)` a
  SIGTERM would wait up to a whole interval, or be cut off by the grace period.
- `interval: float` with a default becomes the optional `--interval` option, help text included.
- `test_stops_at_once_on_shutdown` starts the worker with an hour-long interval and expects it to finish
  within a second of `shutdown.set()`.
