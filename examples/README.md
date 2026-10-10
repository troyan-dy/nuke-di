# Examples

Runnable scenarios for `nuke-di`, from a one-file script to a service with an API, a queue worker and a
scheduled job. Every example is a small package with its own README: what the scenario is, how to run it, the
real output, and a test that shows how to check it.

## How to run

From a clone of the repository, install the dependencies once and run everything from this directory:

```console
$ make install
$ cd examples
$ uv run python -m hello.main
database: connected
Hello, user-42!
database: disconnected
```

The tests of every example run with `uv run pytest` from here, or `uv run pytest <example>` for one of
them. The examples with a web server start it with `uv run --with uvicorn ...`, and those with a
message broker need a local NATS:

```bash
docker run -d --rm --name nats -p 4222:4222 nats:2.10
```

## Scenarios

### Basics

| Example                                     | Scenario                                                                    |
|---------------------------------------------|-----------------------------------------------------------------------------|
| [hello](hello/)                             | The smallest program: two clients, `inject()` and `async with DI`           |
| [settings](settings/)                       | Configuration from environment variables as a client, replaced in tests     |
| [not_singleton](not_singleton/)             | `Client` against `NotSingletonClient`: which instances are shared           |
| [dataclass_clients](dataclass_clients/)     | `@client_dataclass` instead of a hand-written `__init__`                    |
| [graph](graph/)                             | Who needs whom in a real tree, its Mermaid diagram and its startup         |
| [startup_failures](startup_failures/)       | A dependency that is down or hangs, and a tree that cannot be built         |

### Scripts and jobs

| Example                                     | Scenario                                                                    |
|---------------------------------------------|-----------------------------------------------------------------------------|
| [plain_script](plain_script/)               | A one-off script with its own container, without `@job`                     |
| [cli_job](cli_job/)                         | A `@job` with command-line parameters, `--help` and exit codes              |
| [http_job](http_job/)                       | An `httpx.AsyncClient` wrapped as a client, requests made concurrently      |
| [hooks_metrics](hooks_metrics/)             | Run hooks, startup timings as metrics and JSON logs                         |

### Workers and queues

| Example                                     | Scenario                                                                    |
|---------------------------------------------|-----------------------------------------------------------------------------|
| [sqlite_queue](sqlite_queue/)               | A task queue on a SQLite table: a job enqueues, a worker consumes           |
| [nats_worker](nats_worker/)                 | A worker subscribed to NATS with nats-py, in a queue group                  |
| [periodic_worker](periodic_worker/)         | Work every N seconds that stops at once on SIGTERM                          |
| [background_tasks](background_tasks/)       | A heartbeat and a refresher next to the main loop; a crash fails the process |

### Frameworks

| Example                                     | Scenario                                                                    |
|---------------------------------------------|-----------------------------------------------------------------------------|
| [fastapi_app](fastapi_app/)                 | FastAPI: routes, a router, a dependency, a websocket and a lifespan         |
| [litestar_app](litestar_app/)               | Litestar with `ClientPlugin`: handlers, a dependency and a controller       |
| [starlette_app](starlette_app/)             | A framework without an integration: `inject()` and a lifespan by hand       |
| [faststream_nats](faststream_nats/)         | FastStream on NATS: a subscriber with clients and a publisher               |
| [taskiq_app](taskiq_app/)                   | taskiq: tasks with clients, a generator dependency, an `InMemoryBroker`      |

### Putting it together

| Example                                     | Scenario                                                                    |
|---------------------------------------------|-----------------------------------------------------------------------------|
| [testing](testing/)                         | A cookbook of tests: `mock()`, `override()`, fixtures, jobs, workers, wiring |
| [service_layout](service_layout/)           | One service, three processes: an API, an outbox worker and a cleanup job, with Kubernetes manifests |
