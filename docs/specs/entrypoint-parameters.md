# Entrypoint parameters

Status: implemented.
Issue: [#3](https://github.com/troyan-dy/nuke-di/issues/3).
Builds on: [workers-and-jobs.md](workers-and-jobs.md).
Terms: see [CONTEXT.md](../../CONTEXT.md). This spec uses **Entrypoint**, **Job**, **Worker**, **Run** and **Parameter** as defined there.

## Problem

An entrypoint function takes only clients, and its configuration comes from environment variables. One-off and backfill runs need a value per Run, e.g. `python -m app.jobs.sync --date 2026-10-01`, and today that value has to be smuggled through an environment variable.

## Goal

Every annotated argument of an entrypoint that is not a client becomes a command-line option:

```python
# app/jobs/sync.py
import datetime
from typing import Annotated

from nuke_di import Option, job

from app.clients import Postgres


@job
async def sync(
    pg: Postgres,
    date: Annotated[datetime.date, Option(help="Day to sync", short="d")],
    tables: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    """Copy one day of changes into the warehouse."""
```

```bash
python -m app.jobs.sync --date 2026-10-01 --tables users --tables orders --dry-run
python -m app.jobs.sync --help
```

The function stays an ordinary coroutine: a test passes Parameters as keyword arguments.

## Non-goals

- **Positional arguments.** Every Parameter is an option, so adding one never breaks an existing command line.
- **Renaming an option.** The long flag is always derived from the argument name.
- **Reading a Parameter from the environment** (`Option(env=...)`). Environment configuration belongs to clients.
- **Subcommands, `Literal`, `dict`, `tuple`, unions of several types, nested lists.**
- **Validating Parameters on import.** The parser is built only under `__main__`.

## Public API

Exported from `nuke_di`.

### Which arguments are Parameters

`inspect.signature` and `get_type_hints(func, include_extras=True)` of the entrypoint are read. For each argument:

- `*args` / `**kwargs` are skipped.
- An argument without a type hint raises `InvalidSignatureError`, as `inject()` already does.
- A client (a subclass of `NotSingletonClient`, possibly inside `Annotated`) is resolved by the container as before.
- Anything else is a Parameter. A positional-only Parameter raises `InvalidSignatureError`, because Parameters are passed as keyword arguments.

### `Option`

```python
@dataclass(frozen=True)
class Option:
    help: str | None = None
    short: str | None = None  # one letter or digit, e.g. "d" for "-d"
```

Optional metadata for a Parameter, given through `Annotated[T, Option(...)]`. Other `Annotated` metadata is ignored. `Option` on a client, or a `short` that is not one alphanumeric character, raises `InvalidSignatureError`.

### `UsageError`

```python
class UsageError(Exception):
    usage: str  # the usage line of the parser
```

The command line does not match the Parameters: an unknown option, a missing required one, an invalid value. It is recorded as `Run.error`, so hooks can tell it apart with `isinstance`.

## Mapping

- The flag is `--` plus the argument name with `_` replaced by `-`; `dest` is the argument name. `Option.short` adds `-<short>`.
- An argument without a default is a required option. An argument with a default is optional, and the default is appended to its help as `(default: …)`, an `Enum` member shown by name.
- The parser never sets defaults (`default=argparse.SUPPRESS`): an option that is not given is left out, and Python applies the function's default.
- `T | None` and `Optional[T]` are unwrapped to `T`. `Annotated` is unwrapped outside and inside the union.

| `T` | Option | Parsed by |
| --- | --- | --- |
| `str`, `int`, `float`, `pathlib.Path` | `--x VALUE` | `T(value)` |
| `bool` | `--x` / `--no-x` (`BooleanOptionalAction`); `-s` gives `True` | flag |
| `datetime.date`, `datetime.datetime` | `--x 2026-10-01` | `T.fromisoformat`, errors read `invalid date value: 'x'` |
| `Enum` subclass | `--x {RED,GREEN}` | member **name**, case-sensitive; errors read `invalid choice: 'x' (choose from RED, GREEN)` |
| `list[T]` for a `T` above except `bool` | repeated: `--x a --x b` (`action="append"`) | each item as `T`; without a default, at least one is required |

Any other type raises `InvalidSignatureError`. So does a flag that clashes with another one, including `-h` / `--help`.

The parser:

- `prog` is `python -m <module>` under `python -m` (a trailing `.__main__` dropped), otherwise the argparse default;
- `description` is `inspect.getdoc(func)`;
- `allow_abbrev=False`, so `--da` is not silently taken for `--date`;
- reads `sys.argv[1:]`.

An entrypoint without Parameters still gets a parser: `--help` works and any argument is a usage error.

## The Run

Parsing happens in the decorator, before the Run:

1. Build the parser and parse `sys.argv[1:]`. Three outcomes:
   - **`--help`**: argparse prints the help, the process exits with `0`. No Run is created and no hook is called: nothing ran.
   - **An error**: a `UsageError`, an `InvalidSignatureError`, or any exception while reading the signature, e.g. an unresolvable forward reference. The error is kept for step 2. A `UsageError` is printed to stderr the way argparse prints it: the usage line, then `<prog>: error: <message>`.
   - **Parameters**: a dict of the options that were given.
2. The Run starts as before: create the `Run`, call every `on_start`.
3. With a kept error, `run.error` is set and the Run goes straight to `on_finish`: nothing is resolved or connected and no signal handler is installed. Otherwise the Run continues as in workers-and-jobs.md and calls the injected function with the Parameters as keyword arguments.

### Exit codes

The first matching rule wins:

| Condition | Exit code |
| --- | --- |
| `UsageError` | `2` |
| Any other exception: the signature, resolution, connect, the entrypoint, a Background task | `1` |
| A termination signal was received | `128 + signum` |
| Otherwise | `0` |

### Logging

A `UsageError` is logged at `ERROR` without a traceback: argparse has already printed the reason. Every other failure keeps its traceback.

## Changes to existing behavior

- An entrypoint used to ignore its command line. Now an unknown argument fails the Run with exit code `2`, and `--help` prints the help instead of running. This goes into the CHANGELOG under **Changed**.
- An argument without a type hint is now found while the parser is built, before the Run starts. Hooks still see the `InvalidSignatureError` and the exit code stays `1`.

## Testing

- **Unit** (`tests/test_cli.py`): every row of the mapping table, defaults left to the function, required options, `Optional`, nested `Annotated`, `list` with and without a default, help text and defaults in it, each `InvalidSignatureError` case, `UsageError` for unknown, missing and invalid values, `--help`.
- **Run** (`tests/test_run.py`): a kept `UsageError` gives exit code `2`, hooks see it, the container is untouched; Parameters reach the function.
- **Decorator** (`tests/test_entrypoint.py`): `--help` exits with `0` without calling hooks; a bad argument exits with `2`; a broken signature exits with `1` and hooks see it.
- **Subprocess** (`tests/test_processes.py`): Parameters reach a real `python -m` job; `--help` → `0`; an invalid value → `2` with the usage on stderr and no client connected.

Coverage stays at 100%, line and branch.

## Documentation

- **README**: a "Parameters" subsection under "Workers and jobs", the new exit code row, `UsageError` in the Errors table.
- **CONTEXT.md**: the term **Parameter**.
- **CHANGELOG**: `Added` for Parameters, `Option` and `UsageError`; `Changed` for the command line no longer being ignored.
- **Version**: minor bump.

## Acceptance criteria

- [x] Non-client arguments of `@job` and `@worker` functions are filled from the command line, following the mapping table.
- [x] `--help` prints the generated help and exits with `0` without starting a Run.
- [x] An invalid command line exits with `2`, prints the argparse usage and error, connects no client and is seen by hooks as a `UsageError`.
- [x] An unsupported Parameter type, `Option` on a client or a clashing flag exits with `1` as `InvalidSignatureError`.
- [x] The decorated function is still returned unchanged on import and callable with Parameters as keyword arguments.
- [x] No new runtime dependencies; `make check` passes with 100% coverage.
- [x] The README, CONTEXT.md, CHANGELOG and version are updated.
