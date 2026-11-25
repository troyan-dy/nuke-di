# Testing

A cookbook of testing techniques on one small app: signups are registered by a handler and by a
worker, and a job reminds the users who have not confirmed their email. Every technique is one
test function with a one-line docstring.

| File                       | What it holds                                                          |
|----------------------------|------------------------------------------------------------------------|
| `clients.py`               | `Database`, `Mailer`, `Queue` and `Signups` that depends on the first two |
| `main.py`                  | The handler `register` and `main()`, which uses the global `DI`        |
| `jobs/reminders.py`        | The `@entrypoint` `reminders`, with defaults for its ordinary arguments                  |
| `workers/signups.py`       | The `@entrypoint` `signups` registers the next signup     |
| `tests/test_clients.py`    | Mocks, fakes and `override()`, on the `di` fixture and the global `DI` |
| `tests/test_entrypoints.py`| The job and the worker, called directly and through a container        |
| `tests/test_wiring.py`     | Each entrypoint resolves its dependencies                    |

## The techniques

| Technique | Test | When to use |
|-----------|------|-------------|
| `di.mock(client_class)`: an autospec mock, `return_value`, `await_args_list` | `test_mock_autospec` | The default: replace a client in a fresh container and check how it was called |
| The autospec follows the real class | `test_mock_follows_the_real_signatures` | A typo in a method name or a wrong call fails the test instead of passing silently |
| `DI.override(client_class, Fake())` with a hand-written subclass | `test_override_with_fake` | A fake with behavior (records, stores) is clearer than a configured mock; the other clients stay real |
| `override(client_class)` without `new`, as a context manager | `test_override_autospec_across_two_runs` | The test connects the container more than once: a `mock()` would be dropped by the first `disconnect()` |
| The `global_di` fixture | `test_code_that_calls_global_di` | The code under test calls `DI.inject()` / `async with DI` itself |
| An entrypoint called directly with `AsyncMock`s and parameters | `test_job_directly` | Unit-test the logic of a job; parameters are keyword arguments |
| An entrypoint through a container: `di.inject(reminders)` | `test_job_through_container` | Check the job with the clients wired as in production |
| An entrypoint with a fake queue | `test_signup_registers_the_queued_email` | Test one delivery with no broker |
| `Dependencies().inject(entrypoint)`, parametrized | `test_entrypoint_resolves` | One test in CI checks the wiring of every entrypoint, no infrastructure needed |
| The same `inject()` on a broken `__init__` | `test_wiring_catches_a_broken_init` | Shows what the wiring test catches: the error a real run would print |

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
$ uv run python -m testing.jobs.reminders
database: connected
mailer: connected
mailer: 'Please confirm your email' to bob@example.com
mailer: 'Please confirm your email' to carol@example.com
reminders: 2 users
database: disconnected
mailer: disconnected
$ uv run python -m testing.workers.signups
queue: connected
database: connected
mailer: connected
mailer: 'Welcome' to user1@example.com
signups: registered user1@example.com as user 4
queue: disconnected
database: disconnected
mailer: disconnected
```

## Test

```console
$ uv run pytest -q testing
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
