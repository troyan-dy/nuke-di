# FastStream on NATS

A FastStream worker on NATS: `setup(app)` connects the clients before the broker starts, the subscriber
takes a client next to a pydantic message and a dependency that takes another client, and its return
value is published to a second subject. A `@job` sends the test orders.

| File          | What it holds                                                                          |
|---------------|----------------------------------------------------------------------------------------|
| `clients.py`  | `Database` of customers and `Ledger` of totals                                         |
| `messages.py` | The pydantic messages `Order` and `Receipt`                                            |
| `app.py`      | The broker, the app, `handle_order` (orders in, receipts out) and `print_receipt`      |
| `publish.py`  | A `@job` that sends `--count` orders through a `NatsBroker` wrapped as a client        |
| `test_app.py` | `TestNatsBroker` + `TestApp` with `Database` replaced, and the job with a mocked `Nats` |

## Run

Start NATS once:

```bash
docker run -d --rm --name nats -p 4222:4222 nats:2.10
```

The `faststream run` command needs the `faststream[cli]` extra, so the app runs itself with
`asyncio.run(app.run())`:

```console
$ cd examples
$ uv run python -m faststream_nats.app
database: connected
ledger: connected
2026-10-09 19:31:58,568 INFO     - FastStream app starting...
2026-10-09 19:31:58,576 INFO     - faststream_nats.receipts |            - `PrintReceipt` waiting for messages
2026-10-09 19:31:58,578 INFO     - faststream_nats.orders   |            - `HandleOrder` waiting for messages
2026-10-09 19:31:58,578 INFO     - FastStream app started successfully! To exit, press CTRL+C
2026-10-09 19:31:58,861 INFO     - faststream_nats.orders   | 7fbf69d9-0 - Received
order 1: bob paid 9.99
2026-10-09 19:31:58,862 INFO     - faststream_nats.orders   | 7fbf69d9-0 - Processed
2026-10-09 19:31:58,862 INFO     - faststream_nats.orders   | 06cc84ce-c - Received
order 2: bob paid 9.99
2026-10-09 19:31:58,863 INFO     - faststream_nats.orders   | 06cc84ce-c - Processed
2026-10-09 19:31:58,863 INFO     - faststream_nats.orders   | fd2c0eb6-5 - Received
2026-10-09 19:31:58,863 INFO     - faststream_nats.receipts | 7fbf69d9-0 - Received
receipt 1: bob has paid 9.99 in total
2026-10-09 19:31:58,863 INFO     - faststream_nats.receipts | 7fbf69d9-0 - Processed
order 3: bob paid 9.99
2026-10-09 19:31:58,863 INFO     - faststream_nats.orders   | fd2c0eb6-5 - Processed
2026-10-09 19:31:58,864 INFO     - faststream_nats.receipts | 06cc84ce-c - Received
receipt 2: bob has paid 19.98 in total
2026-10-09 19:31:58,864 INFO     - faststream_nats.receipts | 06cc84ce-c - Processed
2026-10-09 19:31:58,864 INFO     - faststream_nats.receipts | fd2c0eb6-5 - Received
receipt 3: bob has paid 29.97 in total
2026-10-09 19:31:58,864 INFO     - faststream_nats.receipts | fd2c0eb6-5 - Processed
2026-10-09 19:31:59,125 INFO     - faststream_nats.orders   | 093d1869-1 - Received
order 1: guest paid 9.99
2026-10-09 19:31:59,125 INFO     - faststream_nats.orders   | 093d1869-1 - Processed
2026-10-09 19:31:59,126 INFO     - faststream_nats.receipts | 093d1869-1 - Received
receipt 1: guest has paid 9.99 in total
2026-10-09 19:31:59,126 INFO     - faststream_nats.receipts | 093d1869-1 - Processed
^C
2026-10-09 19:32:00,384 INFO     - FastStream app shutting down...
2026-10-09 19:32:00,386 INFO     - FastStream app shut down gracefully.
database: disconnected
ledger: disconnected, totals {'bob': '29.97', 'guest': '9.99'}
```

In another terminal, from `examples/` too:

```console
$ uv run python -m faststream_nats.publish --count 3 --customer-id 2
nats: connected
published order 1
published order 2
published order 3
nats: disconnected
$ uv run python -m faststream_nats.publish --count 1 --customer-id 9
nats: connected
published order 1
nats: disconnected
$ uv run python -m faststream_nats.publish --help
usage: python -m faststream_nats.publish [-h] [--count COUNT]
                                         [--customer-id CUSTOMER_ID]

Send test orders to the faststream_nats.orders subject.

options:
  -h, --help            show this help message and exit
  --count COUNT         How many orders to send (default: 3)
  --customer-id CUSTOMER_ID
                        The customer-id header (default: 1)
```

## Test

No NATS is needed: `TestNatsBroker` routes the messages in memory.

```console
$ uv run pytest -q faststream_nats
..                                                                       [100%]
2 passed in 0.17s
```

## What to look at

- `handle_order(order: Order, ledger: Ledger, customer: Annotated[str, Depends(customer_name)])`: the
  message is the only argument FastStream fills from the body; `ledger` is a client, and `customer_name`
  takes the `customer-id` header and `db: Database`.
- A dependency that took a field of the body, `customer_id: int`, would make FastStream spread the body
  over the arguments, and `order: Order` would no longer match; that is why the customer travels in a
  header.
- `@broker.publisher("faststream_nats.receipts")` forwards the returned `Receipt`, and the second
  subscriber of the same app picks it up.
- The test starts the app with `TestApp` inside `TestNatsBroker`: the test broker alone runs no app
  hooks, so the clients would not connect. The app module holds one broker for every test, so the tests
  that start it run one after another, never two at once.
- `publish.py` is a `@job` with a NATS connection wrapped as a `Client`; `--count` and `--customer-id`
  come from its signature.
