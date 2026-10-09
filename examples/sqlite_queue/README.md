# SQLite queue

A task queue on one SQLite table: a `@job` puts tasks in, a `@worker` takes them out one at a time.
No broker to install, so it is the place to see how a worker polls, finishes its current task on Ctrl+C
and wakes up at once when it is stopped while waiting. Use the same shape for a queue on a Postgres table.

| File            | What it holds                                                                 |
|-----------------|-------------------------------------------------------------------------------|
| `clients.py`    | `Database` around a `sqlite3` connection, `TaskQueue` with `put` / `claim` / `done` on top of it |
| `enqueue.py`    | The `enqueue` job: `--count` and `--kind` add tasks                           |
| `consumer.py`   | The `consume` worker: claims a task, processes it, marks it done, polls when empty |
| `test_queue.py` | The queue on a temporary file, the job and the worker through `di`, the worker with fakes |

The database file is `$QUEUE_DB`, by default `nuke-di-sqlite-queue.db` in the system temp directory.

## Run

Add five tasks, then process them; Ctrl+C in the middle of the fifth:

```console
$ cd examples
$ uv run python -m sqlite_queue.enqueue --count 3
database: connected
queue: connected
enqueue: added task 1 (email-1)
enqueue: added task 2 (email-2)
enqueue: added task 3 (email-3)
enqueue: 3 tasks pending
queue: disconnected
database: disconnected
$ uv run python -m sqlite_queue.enqueue -n 2 --kind report
database: connected
queue: connected
enqueue: added task 4 (report-1)
enqueue: added task 5 (report-2)
enqueue: 5 tasks pending
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
consumer: processing task 4 (report-1)
consumer: done task 4
consumer: processing task 5 (report-2)
^C
consumer: done task 5
consumer: stopped
queue: disconnected
database: disconnected
$ echo $?
130
```

On the drained queue the worker polls every `--poll-interval` seconds (2 by default); Ctrl+C ends the
wait at once instead of after the rest of the interval:

```console
$ uv run python -m sqlite_queue.consumer
database: connected
queue: connected
consumer: queue is empty, waiting
^C
consumer: stopped
queue: disconnected
database: disconnected
$ echo $?
130
```

## Test

```console
$ uv run pytest -q sqlite_queue
....                                                                     [100%]
4 passed in 1.57s
```

## What to look at

- `Database` stores the path in `__init__` and opens the file in `connect()`; `TaskQueue` declares
  `db: Database` and creates its table in its own `connect()`, which runs after the database is open.
- `claim()` is one `UPDATE ... RETURNING` statement, so several consumers on the same file never take the
  same task.
- The empty-queue sleep is `asyncio.wait_for(shutdown.wait(), timeout=poll_interval)`: a plain
  `asyncio.sleep()` would hold the process for the rest of the interval after Ctrl+C.
- A task already claimed is finished on Ctrl+C or SIGTERM; a task left `running` by a crash (or a worker
  killed after `SHUTDOWN_GRACE_SECONDS`) would need a reaper, which this example leaves out.
- `test_queue.py` runs the real job and worker against a temporary `QUEUE_DB` and stops the worker with
  `Shutdown.set()` once the queue is drained.
