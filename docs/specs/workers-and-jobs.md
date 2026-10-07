# Workers and jobs

Status: implemented.
Prerequisite: [#1](https://github.com/troyan-dy/nuke-di/issues/1), which cleans up connected clients when `connect()` fails or is cancelled.
Terms: see [CONTEXT.md](../../CONTEXT.md). This spec uses **Entrypoint**, **Job**, **Worker**, **Run**, **Shutdown** and **Background task** as defined there.

## Problem

`nuke-di` builds the client tree and drives `connect()` / `disconnect()`, but every program that uses it writes the same `main` by hand: inject the function, `async with DI`, call it, translate the outcome into an exit code, catch termination signals so that clients still disconnect, and bound the shutdown so that the orchestrator does not have to kill the process. Most hand-written versions get at least one of these wrong. The usual failures are a SIGTERM that skips `disconnect()`, a crashed loop that leaves a live process doing nothing, and a successful exit code for work that was interrupted.

## Goal

A module becomes a runnable program with one decorator:

```python
# app/jobs/sync.py
from nuke_di import job

from app.clients import Postgres, Warehouse


@job
async def sync(pg: Postgres, warehouse: Warehouse) -> None:
    for batch in await warehouse.changed_batches():
        await pg.upsert(batch)
```

```bash
python -m app.jobs.sync
```

```python
# app/workers/consumer.py
from nuke_di import Shutdown, worker

from app.clients import Queue


@worker
async def consumer(queue: Queue, shutdown: Shutdown) -> None:
    while not shutdown.is_set():
        message = await queue.get()
        await message.process()
        await message.ack()
```

```bash
python -m app.workers.consumer
```

## Non-goals

- **Scheduling.** The library never decides when a job runs. A Kubernetes CronJob, a systemd timer or crontab starts the process; retries and overlap policy are the scheduler's job too.
- **Command-line arguments.** An entrypoint function takes only clients; configuration comes from the environment. Tracked separately in [#3](https://github.com/troyan-dy/nuke-di/issues/3).
- **Scaffolding.** No command generates entrypoint files; the README example is the template.
- **A generic runner CLI** (`nuke-di run module:func`). See [ADR-0002](../adr/0002-entrypoint-runs-on-decoration.md).
- **Restarting a worker in-process.** A worker that ends ends the process; restarting is the orchestrator's job.
- **Built-in metrics or tracing.** Run hooks are the extension point.
- **Synchronous entrypoints.**

## Public API

All new names are exported from `nuke_di`.

### `job` and `worker`

```python
@job
async def f(...) -> None: ...

@job(hooks=[MetricsHook()])
async def f(...) -> None: ...
```

`worker` has the same two forms. Both decorators:

1. Raise `TypeError` at decoration time if the function is not a coroutine function (`inspect.iscoroutinefunction`). This happens on every import, not only under `__main__`, so tests catch it.
2. If `func.__module__ != "__main__"`, return `func` unchanged. `hooks` are ignored, nothing is resolved or registered.
3. Otherwise start the Run immediately, inside the decorator, and end the process with `sys.exit(exit_code)`. Control never returns to the module.

Consequences that must be documented in the README:

- Code above the decorator runs; code below it never does. **One entrypoint per module, defined last.**
- The entrypoint always uses the global `DI` container.
- Every argument of the function must be an annotated client, as `DI.inject()` already requires.

The return value of the function is ignored.

### `Shutdown`

A `Client` that every entrypoint and every client can depend on:

```python
class Shutdown(Client):
    def is_set(self) -> bool: ...
    async def wait(self) -> None: ...
    def set(self) -> None: ...
```

It is set by the Run when the first termination signal arrives; `set()` exists for tests: it only tells cooperative code to finish and does not start the grace period. Outside a Run nothing sets it, so a long-running loop that depends on it works unchanged in other contexts, e.g. inside a web application, and simply never sees a Shutdown.

### `BackgroundTasks`

A `Client` that supervises Background tasks:

```python
class BackgroundTasks(Client):
    def spawn(self, coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task[Any]: ...
    def watch(self, callback: Callable[[BaseException], object]) -> None: ...
    async def stop(self) -> None: ...
```

- `spawn()` schedules the coroutine and keeps a strong reference to the task until it finishes.
- `spawn()` raises `RuntimeError` once the supervisor has started stopping. The coroutine is closed so that no "never awaited" warning is emitted.
- A task that raises (anything except `CancelledError`) is logged with its traceback under the `nuke_di` logger.
- Inside a Run, a failed Background task also fails the Run. The entrypoint is cancelled without a grace period and the exit code is `1`. Outside a Run the failure is only logged.
- `watch()` registers a callback that receives the exception of every failed task; the Run uses it to fail itself.
- `stop()` cancels every running task and awaits all of them; `disconnect()` calls it. This makes the supervisor safe to use under a plain `async with DI` too.

### Run hooks

```python
class RunHook(Protocol):
    async def on_start(self, run: Run) -> None: ...
    async def on_finish(self, run: Run) -> None: ...


@dataclass
class Run:
    name: str  # e.g. "app.jobs.sync.sync"
    kind: Literal["job", "worker"]
    started_at: datetime  # UTC
    finished_at: datetime | None  # set before on_finish
    exit_code: int | None  # set before on_finish
    error: BaseException | None  # the exception that made exit_code 1, if any
    signal: int | None  # the first termination signal received, if any
```

- Hooks are plain objects, not clients. They are not resolved, connected or injected, and they own their own resources.
- `on_start` is called in list order before anything else in the Run, including resolution. `on_finish` is called in reverse order after the clients have disconnected, so it sees the final exit code. Connect failures included.
- An exception raised by a hook is logged and ignored. It never changes the exit code, and the remaining hooks still run.
- `name` is the entrypoint's importable module name plus its function name. Under `python -m` the function's `__module__` is `"__main__"`, so the module name comes from `sys.modules["__main__"].__spec__.name`. When the file is run by path (`python app/jobs/sync.py`), `__spec__` is `None`, and the file stem is used instead.

## The Run

### Sequence

1. Create the `Run`, call every `on_start`.
2. Install signal handlers (see below).
3. `DI.inject(func)` to resolve the tree. Resolution errors (`InitializeDependencyError`, `InvalidSignatureError`) give exit code `1`.
4. `DI.connect()`. A `ConnectError` gives exit code `1`. A signal during connect cancels it: with #1 the connected clients are cleaned up, and the Run continues at step 8 with exit code `128 + signum`.
5. Run the entrypoint function as a task.
6. Wait until the task ends, a Background task fails, or a Shutdown begins.
7. On Shutdown, set `Shutdown` and wait up to `SHUTDOWN_GRACE_SECONDS` for the task to end on its own. Then cancel it and await it.
8. Stop `BackgroundTasks`: cancel and await its tasks. This happens before the container disconnects, because the supervisor itself sits in layer 0 and would otherwise outlive the clients its tasks use.
9. `DI.disconnect()`, with `DISCONNECT_TIMEOUT_SECONDS` per client.
10. Remove the signal handlers. Set `finished_at` and `exit_code`, then call every `on_finish`.
11. `sys.exit(exit_code)`.

A **job** that returns proceeds from step 6 straight to step 8. A **worker** that returns or raises without a Shutdown does the same: the process ends, and the orchestrator decides whether to restart it.

`resolve`, `inject` and `mock` must all happen before step 4, as the container already requires. An entrypoint module may call `DI.mock()` above the decorator, e.g. for a local dry run.

### Signals

- On POSIX, SIGTERM and SIGINT are handled through `loop.add_signal_handler`.
- On Windows only SIGINT is supported, through `signal.signal`, handing over to the loop with `call_soon_threadsafe`. SIGTERM on Windows keeps its default behavior. The README states this.
- **First signal:** begin the Shutdown and record `run.signal`.
- **Second signal during the grace period:** cancel the entrypoint immediately.
- **Signals after the entrypoint has ended** (steps 8–10) are ignored: disconnect is already bounded by its timeout.
- Because SIGINT is handled, `KeyboardInterrupt` is not raised inside a Run.

### Exit codes

The first matching rule wins:

| Condition | Exit code |
| --- | --- |
| An exception other than `CancelledError`: in resolution, connect, the entrypoint, or a Background task | `1` |
| A termination signal was received | `128 + signum` (`143` for SIGTERM, `130` for SIGINT) |
| Otherwise | `0` |

A job that sees a Shutdown and returns cleanly still exits with `128 + signum`, because its work was interrupted and a scheduler must not count it as complete. Exceptions raised by `disconnect()` or by hooks are logged and do not affect the exit code.

### Logging

Under the `nuke_di` logger:

- `INFO` when the Run starts, with the entrypoint name and kind;
- `INFO` when a Shutdown begins, with the signal name;
- `WARNING` when the grace period expires and the entrypoint is cancelled;
- `ERROR` with the traceback when the Run fails;
- `INFO` when the Run finishes, with the exit code and duration.

## Settings

| Environment variable | Default | Where | Description |
| --- | --- | --- | --- |
| `DISCONNECT_TIMEOUT_SECONDS` | `10` | `DependenciesSettings.disconnect_timeout` | Timeout for a single client's `disconnect()`. On timeout the error is logged and disconnecting continues, as it already does for exceptions. Applies to every container, not only Runs. |
| `SHUTDOWN_GRACE_SECONDS` | `10` | `RunSettings.shutdown_grace`, read when the Run starts | How long an entrypoint may keep running after a Shutdown begins before it is cancelled. |

Both are floats; negative values raise `ValueError`.

In the worst case a Run stops in `SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × layers`. With the defaults, a two-layer tree takes the whole Kubernetes default `terminationGracePeriodSeconds` of 30 seconds. The README should give this formula so that deeper trees can tune the timeouts or the grace period.

## Changes to existing behavior

- `DISCONNECT_TIMEOUT_SECONDS` is new and applies to every `disconnect()`. Before, a hung client blocked shutdown forever. This is a behavior change and goes into the CHANGELOG under **Changed**.
- #1 changes `connect()` failure and cancellation handling. It ships before or together with this feature.

## Testing

The entrypoint function itself is tested like any coroutine: the test calls it directly with mocks, because importing its module returns it unchanged.

The Run is tested in two layers:

- **In-process.** The Run logic lives in a function that takes the entrypoint, kind and hooks and returns the exit code. The decorator only adds the `__main__` check and `sys.exit`. Tests drive that function with a fresh container, fake clients and signals sent with `os.kill(os.getpid(), ...)`, or by triggering the handler directly.
- **Subprocess.** A handful of end-to-end tests run `python -m <fixture module>` and assert on the exit code and the log output:
  - success → `0`;
  - an exception → `1`;
  - a connect failure → `1`, with the already-connected clients disconnected;
  - SIGTERM with a cooperative worker → `143`, with every client disconnected;
  - SIGTERM with a worker that ignores Shutdown → cancelled after the grace period, `143`;
  - a second SIGINT → immediate cancellation, `130`;
  - a crashing Background task → `1`.

  Signal tests are skipped on Windows, except the SIGINT ones.

Coverage stays at 100%, line and branch.

## Documentation

- **README**: a "Workers and jobs" section covering the two examples, the one-entrypoint-last rule, Shutdown and the grace period, `BackgroundTasks`, hooks, exit codes, the shutdown time budget and the Windows limitation. The Configuration table gets both new variables.
- **CHANGELOG**: under **Unreleased**, `Added` for `job`, `worker`, `Shutdown`, `BackgroundTasks`, `RunHook`, `Run` and `SHUTDOWN_GRACE_SECONDS`; `Changed` for `DISCONNECT_TIMEOUT_SECONDS`.
- **Version**: minor bump.

## Acceptance criteria

- [ ] `@job` and `@worker`, with and without `hooks=`, run the entrypoint under `python -m` and return the function unchanged on import.
- [ ] Decorating a non-async function raises `TypeError` on import.
- [ ] Exit codes follow the table above in every case listed under Testing.
- [ ] `Shutdown` is set on the first signal; the entrypoint is cancelled after `SHUTDOWN_GRACE_SECONDS` or on the second signal.
- [ ] Background tasks are cancelled and awaited before any client disconnects; a failing Background task fails the Run.
- [ ] Every client that connected is disconnected in every outcome, including a signal during connect.
- [ ] `disconnect()` of a single client is bounded by `DISCONNECT_TIMEOUT_SECONDS`.
- [ ] Hooks see every outcome, including connect failures; a failing hook does not change the exit code.
- [ ] No new runtime dependencies; `make check` passes with 100% coverage.
- [ ] The README, CHANGELOG and version are updated.
