# Service layout

How a real service is laid out: one codebase, one set of clients, several processes. The API
creates orders and writes an outbox row in the same transaction, a worker relays the outbox to
the broker, and a nightly job deletes the rows that were sent long ago.

```text
service_layout/
├── clients.py          Settings, Database (sqlite3), Broker, and the repositories Outbox and Orders
├── models.py           Order and OutboxRow, what the repositories hand out
├── api.py              FastAPI: POST /orders, GET /orders/{id}, GET /health
├── workers/outbox.py   relay loop: pending outbox rows -> Broker, then marked sent
├── jobs/cleanup.py     @entrypoint cleanup: deletes old sent rows
├── k8s.yaml            Deployments for the API and the worker, a CronJob for the job
└── tests/
    ├── test_wiring.py  every entrypoint resolves: the worker, the job, the app's startup
    ├── test_api.py     TestClient against a SQLite file in tmp_path
    └── test_outbox.py  worker stop and job behavior on SQLite
```

Every process takes only the clients it needs, and the container connects only those: the API
connects `Settings`, `Database`, `Outbox` and `Orders`; the worker adds `Broker`; the job needs
no broker at all. The database path comes from `SERVICE_DB` (default: `service_layout_orders.db`
in the system temp directory); `OUTBOX_POLL_SECONDS` sets how often the idle worker looks for new
rows.

## Run

From `examples/`, each process in its own terminal, sharing one fresh database:

```console
$ cd examples
$ export SERVICE_DB=$(mktemp -d)/orders.db
```

The API (uvicorn is not a dependency of the project, `--with` brings it for this run):

```console
$ uv run --with uvicorn uvicorn service_layout.api:app
INFO:     Started server process [65012]
INFO:     Waiting for application startup.
database: connected
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:53001 - "POST /orders HTTP/1.1" 201 Created
INFO:     127.0.0.1:53003 - "POST /orders HTTP/1.1" 201 Created
INFO:     127.0.0.1:53005 - "GET /orders/1 HTTP/1.1" 200 OK
INFO:     127.0.0.1:53007 - "GET /orders/3 HTTP/1.1" 404 Not Found
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
database: disconnected
INFO:     Application shutdown complete.
INFO:     Finished server process [65012]
```

The requests:

```console
$ curl -s localhost:8000/orders -H 'Content-Type: application/json' -d '{"product": "book", "quantity": 2}'
{"id":1,"product":"book","quantity":2}
$ curl -s localhost:8000/orders -H 'Content-Type: application/json' -d '{"product": "pen", "quantity": 10}'
{"id":2,"product":"pen","quantity":10}
$ curl -s localhost:8000/orders/1
{"id":1,"product":"book","quantity":2}
$ curl -s localhost:8000/orders/3
{"detail":"order 3 not found"}
```

The worker relays both events and waits for new rows. Send SIGTERM on POSIX to finish the current
batch and close its clients.

```console
$ uv run python -m service_layout.workers.outbox
broker: connected
database: connected
outbox: relaying
outbox: published row 1 to orders
outbox: published row 2 to orders
outbox: stopped
database: disconnected
broker: disconnected
```

The cleanup job keeps rows that were sent within the past seven days:

```console
$ uv run python -m service_layout.jobs.cleanup
database: connected
cleanup: deleted 0 sent outbox rows older than 7 days
database: disconnected
```

## Test

```console
$ uv run pytest -q service_layout
.......                                                                  [100%]
7 passed in 0.30s
```

## Kubernetes

`k8s.yaml` runs the same image three ways; only the command differs:

| Process | Kubernetes object | Command | Stops on |
|---------|-------------------|---------|----------|
| API     | Deployment `service-layout-api` | `uvicorn service_layout.api:app` | SIGTERM: uvicorn drains, the lifespan disconnects the clients |
| Worker  | Deployment `service-layout-outbox` | `python -m service_layout.workers.outbox` | SIGTERM: the worker finishes its batch and returns |
| Job     | CronJob `service-layout-cleanup`, `0 3 * * *` | `python -m service_layout.jobs.cleanup` | Returns after cleanup; an unhandled error marks the Job failed |

One ConfigMap feeds all three. The API's grace period covers uvicorn's five-second request drain and
the container's fifteen-second disconnect deadline. Set the worker's pod grace period to cover the
time its current batch can take plus the container's disconnect deadline. The per-client disconnect
timeout is five seconds. SQLite on a shared volume keeps the example self-contained; a real service
can use PostgreSQL and drop the volume.

## What to look at

- `clients.py` is the only place that knows about SQLite and the environment: the API, the worker
  and the job import the clients they need and never build them.
- `Orders.create` writes the order and its outbox row in one transaction, through `Outbox.add`, so an
  order is never stored without its event; `Outbox` is a dependency of `Orders`, so it connects first and disconnects last.
- The worker's `main()` sets its own `asyncio.Event` on SIGTERM. The relay waits on that event
  between polls and finishes the current batch before returning.
- The wiring test starts the real app with `override(Database)`, so it catches a broken `__init__`
  anywhere in the routes' trees but the replaced `Database`, with no database file. The API and
  worker tests point `Settings` at a SQLite file in `tmp_path` by passing a real
  `Settings(db_path=...)` as the Replacement.
