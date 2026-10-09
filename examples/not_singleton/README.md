# Client and NotSingletonClient

Two services share one `Settings` (a `Client`) and each gets an `HttpSession` of its own (a
`NotSingletonClient`), so one service can set headers on its session without the other seeing
them. Use a `NotSingletonClient` for state that belongs to one consumer: headers, a counter, a
prefix. It is not a per-request object: the instances are built once, with the tree.

| File           | What it holds                                                                  |
|----------------|--------------------------------------------------------------------------------|
| `main.py`      | `Settings`, `HttpSession`, the two services `Orders` and `Payments`, `handler` |
| `test_main.py` | Which instances are shared, and that each session keeps its own state          |

## Run

```console
$ cd examples
$ uv run python -m not_singleton.main
same Settings:  True
same HttpSession: False
settings: loaded
http session #1: opened
http session #2: opened
session #1 GET https://api.example.com/orders?limit=10 {'X-Caller': 'orders'}
session #2 GET https://api.example.com/balance {'X-Caller': 'payments'}
session #1 GET https://api.example.com/orders?limit=10 {'X-Caller': 'orders'}
session #2 GET https://api.example.com/balance {'X-Caller': 'payments'}
http session #1: closed after 2 requests
http session #2: closed after 2 requests
```

## Test

```console
$ uv run pytest -q not_singleton
..                                                                       [100%]
2 passed in 0.04s
```

## What to look at

- `Settings` is built and connected once; both services and both sessions hold the same object.
- Every consumer that declares `http: HttpSession` gets a new instance, and the container
  connects and disconnects each one: `#1` and `#2`.
- `handler` is called twice and the sessions count 2 requests each: the instances are created
  when the tree is built, not per call. A per-request object is a plain local variable, not a client.
- A `NotSingletonClient` takes dependencies like any client; its `Settings` is the shared one.
