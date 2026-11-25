# SQLite queue

Two executable modules share a task queue on one SQLite table. The first adds tasks, and the second
processes the pending tasks one at a time and returns when the queue is empty.

| File | What it holds |
|------|---------------|
| `clients.py` | The SQLite connection and the queue built on it |
| `enqueue.py` | The entrypoint that adds three example tasks |
| `consumer.py` | The entrypoint that claims, processes and completes each pending task |
| `test_queue.py` | The real queue and both entrypoints on a temporary database |

The database path comes from `QUEUE_DB`. The default is `nuke-di-sqlite-queue.db` in the system temp
directory.

## Run

On a fresh database:

```console
$ cd examples
$ uv run python -m sqlite_queue.enqueue
database: connected
queue: connected
enqueue: added task 1 (email-1)
enqueue: added task 2 (email-2)
enqueue: added task 3 (email-3)
enqueue: 3 tasks pending
queue: disconnected
database: disconnected
$ uv run python -m sqlite_queue.consumer
database: connected
queue: connected
consumer: processing task 1 (email-1)
consumer: done task 1
consumer: processing task 2 (email-2)
consumer: done task 2
consumer: processing task 3 (email-3)
consumer: done task 3
consumer: queue is empty
queue: disconnected
database: disconnected
```

## Test

```console
$ uv run pytest -q sqlite_queue
```

## What to look at

- `Database` stores the path in `__init__` and opens it in `connect()`. `TaskQueue` takes the database
  as a client and creates its table once the connection is open.
- `claim()` uses one `UPDATE ... RETURNING` statement so consumers on the same file claim distinct tasks.
- `test_enqueue_then_consume` imports both entrypoints, injects them against a temporary database and
  verifies that all tasks reach the `done` state.
