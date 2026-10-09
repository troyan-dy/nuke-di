# Hello

The smallest program: two clients, one function that takes a client by its type hint, and
`async with DI` around the call. Start here.

| File           | What it holds                                                       |
|----------------|---------------------------------------------------------------------|
| `main.py`      | `Database`, `UserService` that depends on it, and `handler`         |
| `test_main.py` | The same code with `Database` mocked through the `di` fixture        |

## Run

```console
$ cd examples
$ uv run python -m hello.main
database: connected
Hello, user-42!
database: disconnected
```

## Test

```console
$ uv run pytest -q hello
..                                                                       [100%]
2 passed in 0.04s
```

## What to look at

- `DI.inject(handler)` builds `UserService` and its `Database` from the type hints; `user_id: int`
  is not a client and stays a regular argument.
- `async with DI` calls `connect()` on every client, dependencies first, and `disconnect()` in
  reverse on exit.
- The test never connects the real `Database`: a mock registered before `inject()` replaces it
  for every consumer.
