# CLI job

A `@job` with command-line parameters that exports the orders from a SQLite database as CSV or JSON.
Use it for anything a scheduler or a person starts with options: a report, a backfill, a cleanup. The
signature is the command line: `--help`, validation and exit codes come from the type hints.

| File             | What it holds                                                                  |
|------------------|--------------------------------------------------------------------------------|
| `clients.py`     | `Sqlite`, which wraps `sqlite3.connect` in `connect()` / `disconnect()`, and the `Orders` repository; the first run fills an empty table with 8 demo orders |
| `export.py`      | The `export` job: `--since` (a date), `--limit` (int), `--format` (an Enum), `--output` (`Path \| None`), `--dry-run` (bool) |
| `nightly.sh`     | What a scheduler runs: the job, then a `case` on its exit code                 |
| `test_export.py` | The job called directly with a fake `Orders`, and through `di.inject` with the real clients |

The database lives in the temp directory unless `SQLITE_PATH` says otherwise. The clients log to
stderr, so stdout carries the report alone.

## Run

```console
$ cd examples
$ uv run python -m cli_job.export --since 2026-10-01
sqlite: connected
orders: the table was empty, inserted 8 demo orders
id,customer,amount,created
8,grace,15.0,2026-10-05
7,alan,64.0,2026-10-04
6,barbara,310.0,2026-10-03
5,edsger,7.25,2026-10-02
4,ada,42.0,2026-10-01
export: wrote 5 orders to stdout
sqlite: disconnected
$ uv run python -m cli_job.export -s 2026-10-01 -n 2 -f JSON
sqlite: connected
[
  {
    "id": 8,
    "customer": "grace",
    "amount": 15.0,
    "created": "2026-10-05"
  },
  {
    "id": 7,
    "customer": "alan",
    "amount": 64.0,
    "created": "2026-10-04"
  }
]
export: wrote 2 orders to stdout
sqlite: disconnected
$ uv run python -m cli_job.export --since 2026-10-03 > /tmp/orders.csv
sqlite: connected
export: wrote 3 orders to stdout
sqlite: disconnected
$ cat /tmp/orders.csv
id,customer,amount,created
8,grace,15.0,2026-10-05
7,alan,64.0,2026-10-04
6,barbara,310.0,2026-10-03
$ uv run python -m cli_job.export --since 2026-09-01 --output /tmp/orders.json --format JSON --dry-run
sqlite: connected
export: would write 8 orders to /tmp/orders.json
sqlite: disconnected
```

`--help` is generated from the signature and the docstring, and connects nothing:

```console
$ uv run python -m cli_job.export --help
usage: python -m cli_job.export [-h] -s SINCE [-n LIMIT] [-f {CSV,JSON}]
                                [-o OUTPUT] [--dry-run | --no-dry-run]

Export the orders placed since a day as CSV or JSON.

options:
  -h, --help            show this help message and exit
  -s, --since SINCE     First day of the report, YYYY-MM-DD
  -n, --limit LIMIT     At most this many orders, newest first (default: 100)
  -f, --format {CSV,JSON}
                        Output format (default: CSV)
  -o, --output OUTPUT   File to write; stdout by default
  --dry-run, --no-dry-run
                        Count the orders, write nothing (default: False)
```

A wrong command line fails with exit code `2` before any client is built:

```console
$ uv run python -m cli_job.export
usage: python -m cli_job.export [-h] -s SINCE [-n LIMIT] [-f {CSV,JSON}]
                                [-o OUTPUT] [--dry-run | --no-dry-run]
python -m cli_job.export: error: the following arguments are required: -s/--since
Run cli_job.export.export failed: the following arguments are required: -s/--since
$ echo $?
2
$ uv run python -m cli_job.export --since 2026-10-01 --format csv
usage: python -m cli_job.export [-h] -s SINCE [-n LIMIT] [-f {CSV,JSON}]
                                [-o OUTPUT] [--dry-run | --no-dry-run]
python -m cli_job.export: error: argument -f/--format: invalid choice: 'csv' (choose from CSV, JSON)
Run cli_job.export.export failed: argument -f/--format: invalid choice: 'csv' (choose from CSV, JSON)
$ echo $?
2
```

`nightly.sh` turns the exit code into a decision: `0` done, `2` the command line is wrong and a retry
will not help, `130` / `143` interrupted by a signal and safe to rerun, anything else failed:

```console
$ sh cli_job/nightly.sh 2026-10-01
sqlite: connected
export: wrote 5 orders to /tmp/orders.csv
sqlite: disconnected
nightly: exported
$ echo $?
0
$ sh cli_job/nightly.sh 2026-13-01
usage: python -m cli_job.export [-h] -s SINCE [-n LIMIT] [-f {CSV,JSON}]
                                [-o OUTPUT] [--dry-run | --no-dry-run]
python -m cli_job.export: error: argument -s/--since: invalid date value: '2026-13-01'
Run cli_job.export.export failed: argument -s/--since: invalid date value: '2026-13-01'
nightly: fix the command line, retrying will not help
$ echo $?
2
```

## Test

```console
$ uv run pytest -q cli_job
...                                                                      [100%]
3 passed in 0.05s
```

## What to look at

- Every annotated argument that is not a client is an option: `orders: Orders` is injected, the
  rest is parsed. `Annotated[..., Option(help=..., short=...)]` adds the help text and `-s`.
- An Enum is given by member name (`JSON`, not `json`), `Path | None` is optional with `None` meaning
  stdout, a `bool` gets `--dry-run` / `--no-dry-run`.
- The argument is called `format` because the option name is the argument name; the `noqa: A002`
  accepts the shadowed builtin for the sake of `--format`.
- Importing `cli_job.export` does not run the job, so `test_export.py` calls `export(orders, since=...)`
  with an `AsyncMock(spec=Orders)`, and `di.inject(export)` runs it with the real clients on a
  database in `tmp_path`.
