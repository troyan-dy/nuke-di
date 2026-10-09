# Hooks and startup metrics

A `@job` with a hook that turns every run into Prometheus text lines: the run's duration and exit
code, and how long each client took to connect and disconnect, from `run.clients`. The log of
`nuke_di` goes through a JSON formatter that keeps the structured fields `run`, `client` and
`duration` as keys, and a slow `Kafka` triggers the startup warning. Useful for scheduled jobs
whose startup time you want on a dashboard.

| File                    | What it holds                                                             |
|-------------------------|---------------------------------------------------------------------------|
| `clients.py`            | `Postgres` that connects in 0.1 s and `Kafka` that takes 0.6 s            |
| `observability.py`      | `JsonFormatter`, `setup_logging()` and the `PrometheusMetrics` hook        |
| `export.py`             | The job `export`, with `hooks=[PrometheusMetrics()]`                      |
| `test_observability.py` | The hook on a hand-made failed `Run`, the formatter, the job with mocks    |

## Run

`CONNECT_TIMEOUT_SECONDS=1` makes the 0.6 s of `Kafka` more than half of the timeout, so the
startup logs a `WARNING` that names it. With the default of 30 s there is no warning. The durations
vary from run to run:

```console
$ cd examples
$ CONNECT_TIMEOUT_SECONDS=1 uv run python -m hooks_metrics.export
{"level": "INFO", "logger": "nuke_di.run", "message": "Starting job hooks_metrics.export.export", "run": "hooks_metrics.export.export"}
postgres: connected
kafka: connected
{"level": "INFO", "logger": "nuke_di.core", "message": "Connected 4 clients in 0.60s (slowest: Kafka 0.60s, Postgres 0.10s, Shutdown 0.00s)", "run": "hooks_metrics.export.export", "duration": 0.6013}
{"level": "WARNING", "logger": "nuke_di.core", "message": "Client Kafka took 0.60s to connect, more than half of CONNECT_TIMEOUT_SECONDS (1s)", "run": "hooks_metrics.export.export", "client": "Kafka", "duration": 0.6011}
kafka: orders <- order-1
kafka: orders <- order-2
kafka: orders <- order-3
postgres: disconnected
kafka: disconnected
{"level": "INFO", "logger": "nuke_di.run", "message": "Run hooks_metrics.export.export finished with exit code 0 in 0.653s", "run": "hooks_metrics.export.export", "duration": 0.653}
job_duration_seconds{job="hooks_metrics.export.export"} 0.653
job_exit_code{job="hooks_metrics.export.export"} 0
client_connect_seconds{job="hooks_metrics.export.export",client="Shutdown",outcome="ok"} 0.000
client_disconnect_seconds{job="hooks_metrics.export.export",client="Shutdown",outcome="ok"} 0.000
client_connect_seconds{job="hooks_metrics.export.export",client="BackgroundTasks",outcome="ok"} 0.000
client_disconnect_seconds{job="hooks_metrics.export.export",client="BackgroundTasks",outcome="ok"} 0.000
client_connect_seconds{job="hooks_metrics.export.export",client="Postgres",outcome="ok"} 0.101
client_disconnect_seconds{job="hooks_metrics.export.export",client="Postgres",outcome="ok"} 0.000
client_connect_seconds{job="hooks_metrics.export.export",client="Kafka",outcome="ok"} 0.601
client_disconnect_seconds{job="hooks_metrics.export.export",client="Kafka",outcome="ok"} 0.051
```

A run that fails still reaches `on_finish`. A wrong command line fails before any client is
resolved, so there are no client lines:

```console
$ uv run python -m hooks_metrics.export --limit three
usage: python -m hooks_metrics.export [-h] [--limit LIMIT]
python -m hooks_metrics.export: error: argument --limit: invalid int value: 'three'
{"level": "INFO", "logger": "nuke_di.run", "message": "Starting job hooks_metrics.export.export", "run": "hooks_metrics.export.export"}
{"level": "ERROR", "logger": "nuke_di.run", "message": "Run hooks_metrics.export.export failed: argument --limit: invalid int value: 'three'", "run": "hooks_metrics.export.export"}
{"level": "INFO", "logger": "nuke_di.run", "message": "Run hooks_metrics.export.export finished with exit code 2 in 0.000s", "run": "hooks_metrics.export.export", "duration": 0.0002}
job_duration_seconds{job="hooks_metrics.export.export"} 0.000
job_exit_code{job="hooks_metrics.export.export"} 2
$ echo $?
2
```

## Test

```console
$ uv run pytest -q hooks_metrics
...                                                                      [100%]
3 passed in 0.05s
```

## What to look at

- `on_finish` runs after the clients have disconnected, so `run.clients` holds both phases of every
  client; a client that never started has `connect is None` and gets no line.
- Every run connects its own `Shutdown` and `BackgroundTasks`, so they are in the metrics too.
- The formatter reads the fields with `hasattr()`: `client` is set only on records about one
  client, `run` on every record inside a job.
- A hook is a plain object with `on_start` / `on_finish`, not a client, and a test calls it with a
  `Run` built by hand: no process, no container.
- `setup_logging()` runs only under `python -m`, so the test that imports `export` leaves the
  logging of pytest alone.
