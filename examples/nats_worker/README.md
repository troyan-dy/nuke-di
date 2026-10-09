# NATS worker

A worker that consumes JSON order events from a real NATS subject with `nats-py` directly, no framework
in between, and a job that publishes test orders. Use it as the shape of any long-running consumer of a
broker: the connection is a client, the subscription lives in the worker, Ctrl+C finishes what was
already delivered.

| File             | What it holds                                                                     |
|------------------|-----------------------------------------------------------------------------------|
| `clients.py`     | `Nats` around a `nats-py` connection, `Orders` where handled orders go, the `Order` model |
| `worker.py`      | `handle()` for one message and the `consume` worker that subscribes in a queue group |
| `publish.py`     | The `publish` job: `--count` orders to `nats_worker.orders`                       |
| `test_worker.py` | `handle()` and `consume()` with fakes, and the startup failing at once without NATS; no server needed |

The worker subscribes in the queue group `nats_worker`: NATS delivers each message to only one member of
a group, so several replicas of the worker share the load instead of each getting every order.

## Run

Start NATS (`NATS_URL` points elsewhere, `nats://localhost:4222` by default):

```bash
docker run -d --rm --name nuke-di-examples-nats -p 4222:4222 nats:2.10
```

In one terminal, start the worker:

```console
$ cd examples
$ uv run python -m nats_worker.worker
nats: connected
worker: listening on nats_worker.orders, queue group nats_worker
```

In another, publish three orders:

```console
$ cd examples
$ uv run python -m nats_worker.publish --count 3
nats: connected
publish: order 1
publish: order 2
publish: order 3
nats: disconnected
```

Back in the first terminal, the orders arrive; Ctrl+C in the middle of order 2. Order 3 was already
delivered to this worker, so it is handled too before the worker stops:

```console
$ uv run python -m nats_worker.worker
nats: connected
worker: listening on nats_worker.orders, queue group nats_worker
worker: order 1: 1 x book
worker: order 1 saved
worker: order 2: 2 x lamp
^C
worker: order 2 saved
worker: order 3: 3 x mug
worker: order 3 saved
worker: stopped
nats: disconnected
$ echo $?
130
```

Without a NATS server the run stops at startup with exit code 1, at once:

```console
$ NATS_URL=nats://localhost:4999 uv run python -m nats_worker.publish
Nats.connect() raised OSError: Multiple exceptions: [Errno 61] Connect call failed ('::1', 4999, 0, 0), [Errno 61] Connect call failed ('127.0.0.1', 4999)
Traceback (most recent call last):
  ...
nuke_di.errors.ConnectError: Nats.connect() raised OSError: Multiple exceptions: [Errno 61] Connect call failed ('::1', 4999, 0, 0), [Errno 61] Connect call failed ('127.0.0.1', 4999)
$ echo $?
1
```

## Test

```console
$ uv run pytest -q nats_worker
....                                                                     [100%]
4 passed in 0.63s
```

## What to look at

- On Shutdown the worker calls `subscription.drain()`: NATS stops sending to this replica, and the
  messages it already received are handled before `consume()` returns. Core NATS has no redelivery, so
  dropping them would lose them.
- `Nats.disconnect()` calls `drain()` on the connection: the publisher's last messages are flushed before
  the process exits, with no explicit `flush()` in the job.
- `nats-py` retries a failed first connection for about two minutes by default. The `error_cb` of `Nats`
  re-raises the error while the connection does not exist yet, so a startup without NATS fails at once
  like any other client; once connected, `nats-py` reconnects on its own.
- `handle()` takes `orders` as an argument and is bound with `functools.partial`, so the tests call it
  with a `Msg` built in memory and no server.
