# Background tasks

A worker whose main loop processes messages while two loops run beside it: a heartbeat and a cache
refresh, both started with `BackgroundTasks.spawn()`. Use it when a worker needs something periodic of
its own; unlike a bare `asyncio.create_task()`, a background task that fails is logged with its
traceback and fails the whole process instead of dying silently.

| File             | What it holds                                                                  |
|------------------|--------------------------------------------------------------------------------|
| `clients.py`     | `Inbox`, an endless in-memory queue, and `Cache`, refreshed in the background  |
| `worker.py`      | `heartbeat()`, `refresh_cache()` and the `process` worker; `--fail-after N` breaks the refresh |
| `test_worker.py` | The tasks spawned by name, the heartbeat, the failing refresh and `watch()`, with fakes |

## Run

The worker logs through `logging.basicConfig` above the decorator, so the `INFO` records of `nuke_di`
are shown. Messages every 0.7 seconds, a heartbeat every second, a refresh every 1.25; Ctrl+C in the
middle of message 5:

```console
$ cd examples
$ uv run python -m background_tasks.worker
INFO  nuke_di.run: Starting worker background_tasks.worker.process
inbox: connected
cache: connected, v1
INFO  nuke_di.core: Connected 4 clients in 0.00s (slowest: Shutdown 0.00s, Inbox 0.00s, BackgroundTasks 0.00s)
worker: message-1 with cache v1
worker: message-2 with cache v1
heartbeat: alive, 2 messages received
refresh: cache v2
worker: message-3 with cache v2
heartbeat: alive, 3 messages received
worker: message-4 with cache v2
refresh: cache v3
worker: message-5 with cache v3
heartbeat: alive, 5 messages received
^C
INFO  nuke_di.run: Shutdown requested by SIGINT
worker: stopped
inbox: disconnected
cache: disconnected
INFO  nuke_di.run: Run background_tasks.worker.process finished with exit code 130 in 3.507s
$ echo $?
130
```

The same worker with a refresh that fails after two refreshes. Nobody pressed Ctrl+C: the failed task
cancels the worker at once, without a grace period, and the process exits with code 1:

```console
$ uv run python -m background_tasks.worker --fail-after 2
INFO  nuke_di.run: Starting worker background_tasks.worker.process
inbox: connected
cache: connected, v1
INFO  nuke_di.core: Connected 4 clients in 0.00s (slowest: Shutdown 0.00s, Inbox 0.00s, BackgroundTasks 0.00s)
worker: message-1 with cache v1
worker: message-2 with cache v1
heartbeat: alive, 2 messages received
refresh: cache v2
worker: message-3 with cache v2
heartbeat: alive, 3 messages received
worker: message-4 with cache v2
refresh: cache v3
worker: message-5 with cache v3
heartbeat: alive, 5 messages received
worker: message-6 with cache v3
ERROR nuke_di.clients: Background task refresh-cache failed
Traceback (most recent call last):
  ...
ConnectionError: the settings service is unreachable
inbox: disconnected
cache: disconnected
ERROR nuke_di.run: Run background_tasks.worker.process failed
Traceback (most recent call last):
  ...
ConnectionError: the settings service is unreachable
INFO  nuke_di.run: Run background_tasks.worker.process finished with exit code 1 in 3.755s
$ echo $?
1
```

## Test

```console
$ uv run pytest -q background_tasks
....                                                                     [100%]
4 passed in 0.09s
```

## What to look at

- The worker declares `tasks: BackgroundTasks` like any client and names every task in `spawn(..., name=...)`:
  the name is what the error log shows.
- The background loops never look at `Shutdown`: when the process stops for any reason they are cancelled
  and awaited before `Inbox` and `Cache` disconnect, so they never touch a closed client.
- `worker: stopped` is missing from the failed run: the worker was cancelled in the middle of message 6,
  since a process whose cache can no longer be refreshed should be restarted, not left running.
- `test_a_failed_task_is_reported` uses `watch()`, the hook a run uses to learn about a failure.
