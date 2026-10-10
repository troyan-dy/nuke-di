# taskiq

Weekly reports sent by taskiq tasks: `setup(broker)` fills the clients of the tasks and of a dependency, and
connects them when the worker starts. The broker is an `InMemoryBroker`, which runs the tasks in the process
that kicks them, so the example needs no queue.

| File          | What it holds                                                                          |
|---------------|----------------------------------------------------------------------------------------|
| `clients.py`  | `Database` of users, `Mailer`, and `Reports` that depends on both                      |
| `tasks.py`    | The broker, `send_report`, `send_reports` and its `notify_admin` generator dependency   |
| `main.py`     | Starts the broker, kicks both tasks and prints their results                           |
| `test_app.py` | The tasks on the broker with `Database` replaced and `Mailer` mocked through `DI.override()` |

## Run

```console
$ cd examples
$ uv run python -m taskiq_app.main
database: connected
mailer: connected
mail to alice: your weekly report
send_report(1): sent to alice
mail to bob: your weekly report
mail to carol: your weekly report
mail to admin: taskiq_app.tasks:send_reports finished
send_reports([2, 3]): 2 reports
database: disconnected
mailer: disconnected
```

## Test

```console
$ uv run pytest -q taskiq_app
.....                                                                    [100%]
5 passed in 1.08s
```

## What to look at

- `send_report(user_id: int, reports: Reports = TaskiqDepends())`: `user_id` comes from `.kiq(1)`, `reports`
  from the container by its type. The default is for type checkers, which read `.kiq()` against the task's
  signature; `examples/ruff.toml` tells ruff's `B008` that `TaskiqDepends()` is safe in a default.
- `notify_admin` is a generator dependency that takes taskiq's `Context` and the `Mailer` client side by side;
  the code after its `yield` runs after the task.
- An `InMemoryBroker` is its own worker: `broker.startup()` connects the clients, `broker.shutdown()`
  disconnects them. `test_task_without_the_worker_is_not_connected` kicks a task without it.
- For a real queue, swap the broker, e.g. `NatsBroker` of taskiq-nats, and run `taskiq worker
  taskiq_app.tasks:broker`: the worker connects the clients, a process that only kicks tasks connects none.
  See [docs/guide/taskiq.md](../../docs/guide/taskiq.md).
