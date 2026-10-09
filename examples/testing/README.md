# Testing

A cookbook of testing techniques on one small app: signups are registered by a handler and by a
worker, and a job reminds the users who have not confirmed their email. Every technique is one
test function with a one-line docstring.

| File                       | What it holds                                                          |
|----------------------------|------------------------------------------------------------------------|
| `clients.py`               | `Database`, `Mailer`, `Queue` and `Signups` that depends on the first two |
| `main.py`                  | The handler `register` and `main()`, which uses the global `DI`        |
| `jobs/reminders.py`        | The `@job` `reminders` with `--limit` and `--dry-run`                  |
| `workers/signups.py`       | The `@worker` `signups`, which registers signups until `Shutdown`     |
| `tests/test_clients.py`    | Mocks, fakes and `override()`, on the `di` fixture and the global `DI` |
| `tests/test_entrypoints.py`| The job and the worker, called directly and through a container        |
| `tests/test_wiring.py`     | Every entrypoint resolves, and the dependency graph                    |

## The techniques

| Technique | Test | When to use |
|-----------|------|-------------|
| `di.mock(cls)`: an autospec mock, `return_value`, `await_args_list` | `test_mock_autospec` | The default: replace a client in a fresh container and check how it was called |
| The autospec follows the real class | `test_mock_follows_the_real_signatures` | A typo in a method name or a wrong call fails the test instead of passing silently |
| `DI.override(cls, Fake())` with a hand-written subclass | `test_override_with_fake` | A fake with behavior (records, stores) is clearer than a configured mock; the other clients stay real |
| `override(cls)` without `new`, as a context manager | `test_override_autospec_across_two_runs` | The test connects the container more than once: a `mock()` would be dropped by the first `disconnect()` |
| The `global_di` fixture | `test_code_that_calls_global_di` | The code under test calls `DI.inject()` / `async with DI` itself |
| A `@job` called directly with `AsyncMock`s and parameters | `test_job_directly` | Unit-test the logic of a job; parameters are keyword arguments |
| A `@job` through a container: `di.inject(job)` | `test_job_through_container` | Check the job with the clients wired as in production |
| A `@worker` stopped by a fake that calls `Shutdown.set()` | `test_worker_stops_on_shutdown` | Test the loop of a worker: `set()` does what SIGTERM would do |
| `Dependencies().inject(entrypoint)`, parametrized | `test_entrypoint_resolves` | One test in CI checks the wiring of every entrypoint, no infrastructure needed |
| The same `inject()` on a broken `__init__` | `test_wiring_catches_a_broken_init` | Shows what the wiring test catches: the error a real run would print |
| `deps.graph()`: layers and dependencies | `test_graph` | Assert on which clients an entrypoint pulls in and in which layer they connect |
| `deps.graph()` with a mock | `test_graph_shows_replacements` | Check that a test really replaced the client it meant to |

## Run

```console
$ cd examples
$ uv run python -m testing.main
database: connected
mailer: connected
mailer: 'Welcome' to dave@example.com
database: disconnected
mailer: disconnected
registered user 4
$ uv run python -m testing.jobs.reminders --limit 1 --dry-run
database: connected
mailer: connected
reminders: would remind bob@example.com
reminders: 1 users
database: disconnected
mailer: disconnected
$ uv run python -m testing.workers.signups
queue: connected
database: connected
mailer: connected
mailer: 'Welcome' to user1@example.com
signups: registered user1@example.com as user 4
mailer: 'Welcome' to user2@example.com
signups: registered user2@example.com as user 5
mailer: 'Welcome' to user3@example.com
signups: registered user3@example.com as user 6
^C
mailer: 'Welcome' to user4@example.com
signups: registered user4@example.com as user 7
signups: stopped
queue: disconnected
database: disconnected
mailer: disconnected
$ echo $?
130
```

## Test

```console
$ uv run pytest -q testing
..............                                                           [100%]
14 passed in 0.08s
```

## What to look at

- A Replacement is never connected: the tests that mock `Database` print no `database: connected`,
  and `test_override_with_fake` connects the real `Database` next to the fake `Mailer`.
- Mocks and overrides are registered before `inject()` / `resolve()`; afterwards they would reach
  only part of the tree, so `mock()` raises instead.
- Importing `jobs/reminders.py` or `workers/signups.py` runs nothing: the decorated functions are
  plain coroutines in a test, and `inject()` builds their trees without connecting them.
- Tests that touch the global `DI` go through `global_di` or a `DI.override()` block, so no
  resolved client leaks into the next test.
