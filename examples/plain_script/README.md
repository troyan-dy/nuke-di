# Plain script

A one-off asyncio script that seeds a SQLite database with demo customers, using its own
`Dependencies()` container and no `@job`. Use this shape for a developer helper, a notebook cell or a
step inside a program that already owns `asyncio.run()` and its command line: when you need the
clients and their lifecycle, but not parameters, signal handling, exit codes or hooks.

| File           | What it holds                                                                    |
|----------------|----------------------------------------------------------------------------------|
| `clients.py`   | `Sqlite`, which wraps `sqlite3.connect` in `connect()` / `disconnect()`, and the `Customers` repository on top of it |
| `seed.py`      | `seed()`, which takes `Customers` by type hint, and `main()` with its own container |
| `test_seed.py` | `seed()` against a real file in `tmp_path`, and with `Customers` mocked          |

## Run

The database lives in the temp directory unless `SQLITE_PATH` says otherwise. Every run starts the
table from scratch:

```console
$ cd examples
$ uv run python -m plain_script.seed
sqlite: connected
seed: 5 customers in /tmp/nuke-di-plain-script.sqlite3
sqlite: disconnected
$ SQLITE_PATH=/tmp/demo.sqlite3 uv run python -m plain_script.seed
sqlite: connected
seed: 5 customers in /tmp/demo.sqlite3
sqlite: disconnected
$ sqlite3 /tmp/demo.sqlite3 "SELECT * FROM customers"
1|Ada Lovelace|ada@example.com
2|Alan Turing|alan@example.com
3|Grace Hopper|grace@example.com
4|Edsger Dijkstra|edsger@example.com
5|Barbara Liskov|barbara@example.com
```

## Test

```console
$ uv run pytest -q plain_script
..                                                                       [100%]
2 passed in 0.04s
```

## What to look at

- `deps.inject(seed)` and `deps.resolve(Sqlite)` build the whole tree before anything connects;
  `async with deps` then connects `Sqlite` and closes it on the way out, even if `seed()` raises.
- `resolve(Sqlite)` returns the very instance `Customers` received: a `Client` is one per
  container, so the script can read `db.path` from the same connection the repository uses.
- `Sqlite.__init__` only reads `SQLITE_PATH`; the file is opened in `connect()`, and the blocking
  `sqlite3` calls go through `asyncio.to_thread`.
- The injected function takes its remaining arguments by keyword (`injected(rows=...)`): the
  clients are bound as keywords, so a positional argument would collide with them.
- Switch to `@job` (see `cli_job/`) once the script needs command-line options, an exit code a
  scheduler can read, or a clean stop on SIGTERM.
