# Bound the whole disconnect() by one deadline

## Overview
- Issue [#31](https://github.com/troyan-dy/nuke-di/issues/31). Today every client's `disconnect()` is bounded by
  `DISCONNECT_TIMEOUT_SECONDS` on its own, and a client disconnects only after its consumers have (ADR-0007), so the
  worst case of `Dependencies.disconnect()` is `DISCONNECT_TIMEOUT_SECONDS × the longest chain of dependencies`. The
  operator setting the variable does not know the depth of the tree; with the defaults a chain of two already takes the
  whole Kubernetes `terminationGracePeriodSeconds` of 30 s, and a deeper tree with a hung client per level is SIGKILLed
  before its lower clients ever disconnect.
- Add a second bound, `DISCONNECT_DEADLINE_SECONDS` / `DependenciesSettings.disconnect_deadline`, **default `20`**:
  one deadline for the whole `disconnect()` (and for the rollback of a failed `connect()`), whatever the shape of the
  tree. `DISCONNECT_TIMEOUT_SECONDS` keeps its meaning: one hung client still cannot eat the budget of the clients
  after it.
- The worst case of a process stop becomes `SHUTDOWN_GRACE_SECONDS + DISCONNECT_DEADLINE_SECONDS` (10 + 20 = 30 with
  the defaults), independent of the tree.
- Applies to every container: Run (workers, jobs), FastAPI/Litestar/ASGI lifespans, taskiq, aiogram, MCP.

## Context (from discovery)
- `src/nuke_di/options.py`: `DependenciesSettings` with `connect_timeout`, `disconnect_timeout`,
  `connect_concurrency`, each read from the environment by a `_..._from_env()` factory, `__post_init__` validates `>= 0`.
- `src/nuke_di/core.py`:
  - `_disconnect_clients()` (~L260) builds the consumers map and runs `_in_dependency_order(..., self._disconnect_client)`;
    used by both `disconnect()` and `_rollback()`; `flush()` in `finally`.
  - `_disconnect_client()` (~L288) takes the `limiter` slot, then
    `with _measure(timing, "disconnect"): await _within(self.settings.disconnect_timeout, client.disconnect())`;
    logs `"%s did not disconnect within %gs (DISCONNECT_TIMEOUT_SECONDS)"` on `TimeoutError`, swallows client
    exceptions and its own `CancelledError`, re-raises a cancellation from outside (`_cancelled_from_outside()`).
  - `_within(seconds, coro)` (~L839): `seconds <= 0` closes the coroutine unstarted and raises `TimeoutError`.
  - `_in_dependency_order()` (~L854): a client's `step` runs once the steps of the clients it waits for have
    *returned*; `_disconnect_client` returns normally after a timeout, so a timed-out client still releases its
    dependencies.
- `src/nuke_di/timings.py`: `ClientTiming.disconnect` / `disconnect_outcome`, `None` when the phase never started.
- Tests: `tests/test_errors.py` (settings from env, default, negative, timeout log), `tests/test_timings.py`
  (`test_timeouts_and_disconnect_failure`, `test_zero_timeout_expires_before_the_client_runs`),
  `tests/test_connect_order.py` (`test_timed_out_disconnect_still_releases_its_dependencies`, the `probe` helper and
  `Tracked` clients), `tests/test_examples.py` (`CONFIGURATION` env vars stripped from example processes).
- Docs carrying the `× the longest chain` formula or the per-client wording: `docs/guide/configuration.md` (table),
  `docs/guide/clients.md` (~L81), `docs/guide/workers-and-jobs.md#grace-period` (~L405) and the Kubernetes manifest
  (~L661), `docs/guide/servers-in-workers.md` (~L25), `docs/guide/taskiq.md` (~L183),
  `examples/service_layout/k8s.yaml` (comments ~L35, ~L67, ConfigMap) and `examples/service_layout/README.md` (~L131).
  `docs/specs/workers-and-jobs.md` is a historical spec and stays as it is.
- ADRs: `docs/adr/0007-connect-by-own-dependencies.md` (disconnect by own consumers); the next free number is 0012.

## Development Approach
- **Testing approach**: TDD — write the failing tests of a task first, then the code that makes them pass.
- Complete each task fully before moving to the next
- Make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - tests are not optional - they are a required part of the checklist
  - write unit tests for new functions/methods
  - write unit tests for modified functions/methods
  - add new test cases for new code paths
  - update existing test cases if behavior changes
  - tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- Run tests after each change (`make test`), lint with `make lint`
- Maintain backward compatibility: `DISCONNECT_TIMEOUT_SECONDS`, `disconnect_timeout`, its log message and the
  `Outcome` literal stay unchanged; no new `Outcome` value is added.
- Keep the apparent simplicity (AGENTS.md): no new public API beyond the one setting.

## Testing Strategy
- **Unit tests**: required for every task (see Development Approach above), pytest with `asyncio` tests as in
  the existing suite; keep timings short (tens of milliseconds) and assert on outcomes and log text, with generous
  upper bounds on wall time only where the point of the test is the bound.
- **E2E tests**: none in this project; `tests/test_examples.py` runs the examples as processes.

## Progress Tracking
- Mark completed items with `[x]` immediately when done
- Add newly discovered tasks with ➕ prefix
- Document issues/blockers with ⚠️ prefix
- Update plan if implementation deviates from original scope
- Keep plan in sync with actual work done

## What Goes Where
- **Implementation Steps** (`[ ]` checkboxes): code, tests, documentation, changelog and version in this repository
- **Post-Completion** (no checkboxes): reviews, PR, merge (= a PyPI release)

## Implementation Steps

### Task 1: Add the `disconnect_deadline` setting
- [x] write failing tests in `tests/test_errors.py`: `DISCONNECT_DEADLINE_SECONDS=2.5` in the environment gives
      `DependenciesSettings().disconnect_deadline == 2.5`; unset gives `20`; `DependenciesSettings(disconnect_deadline=-1)`
      raises `ValueError` matching `disconnect_deadline`; `0` is accepted
- [x] in `src/nuke_di/options.py` add `DISCONNECT_DEADLINE_ENV = "DISCONNECT_DEADLINE_SECONDS"`,
      `DEFAULT_DISCONNECT_DEADLINE = 20.0`, `_disconnect_deadline_from_env()` and the field
      `disconnect_deadline: float` right after `disconnect_timeout`, with a one-line comment (the budget of a whole
      `disconnect()`, seconds)
- [x] validate `disconnect_deadline >= 0` in `__post_init__`, same wording as `disconnect_timeout`
- [x] add `"DISCONNECT_DEADLINE_SECONDS"` to `CONFIGURATION` in `tests/test_examples.py`
- [x] run `make test` - must pass before task 2

### Task 2: Bound every client's disconnect() by the shared deadline
- [x] write failing tests (in `tests/test_connect_order.py`, next to the `HangsOnDisconnect` tests, reusing
      `probe`/`Tracked` where it fits):
  - a chain of three clients that each hang in `disconnect()`, `disconnect_timeout=0.2`, `disconnect_deadline=0.3`:
    `disconnect()` returns in well under `0.6` s (assert `< 0.5`); the top client is `timed_out` by its own timeout,
    the middle one `timed_out` by the deadline, the bottom one never starts (`disconnect_outcome is None`, its
    `disconnect()` never ran)
    - ⚠️ Task 2 asserts only that the bottom's `disconnect()` never ran: until Task 3 skips it, `_within()` with a
      bound `<= 0` records it `timed_out`; the `None` assertion is added in Task 3
  - one hanging client with `disconnect_timeout=10`, `disconnect_deadline=0.05`: `timed_out`, and the log says
    `"<Name> did not disconnect before DISCONNECT_DEADLINE_SECONDS ran out (0.05s)"` (exact wording decided here and
    reused in the docs), not the `DISCONNECT_TIMEOUT_SECONDS` message
  - the deadline not reached: every client `ok`, no deadline record in the log (happy path unchanged)
  - `test_timed_out_disconnect_still_releases_its_dependencies` still passes: within the deadline a timed-out client
    still releases its dependencies
- [x] in `_disconnect_clients()` (`src/nuke_di/core.py`) compute the deadline once,
      `asyncio.get_running_loop().time() + self.settings.disconnect_deadline`, and pass it to `_disconnect_client`
      through the existing `partial(...)`
- [x] in `_disconnect_client()`, after taking the `limiter` slot, compute
      `remaining = deadline - loop.time()` and await `_within(min(self.settings.disconnect_timeout, remaining), ...)`;
      remember which bound was the smaller one so the `TimeoutError` handler logs either the existing
      `DISCONNECT_TIMEOUT_SECONDS` message or the new deadline message (same `logger.exception` level and `_fields`)
- [x] keep the outcome recording in `_measure` as it is (`timed_out` on `TimeoutError`); no per-client cost beyond the
      one subtraction and `min()`
- [x] run `make test` - must pass before task 3

### Task 3: Skip the clients that did not start before the deadline
- [ ] write failing tests:
  - ➕ in `test_deadline_bounds_a_chain_of_hung_disconnects`, assert `HangingBottom`'s `disconnect_outcome is None`
  - in the three-client chain of Task 2, the log has one record listing the skipped client(s):
    `"Skipped disconnecting 1 client after DISCONNECT_DEADLINE_SECONDS (0.3s): Bottom"` (pluralised with `_count`)
  - `disconnect_deadline=0`: no client's `disconnect()` runs, every `disconnect_outcome is None`, one skip record
    lists them all, `dep.connected is False` and a second `connect()` / `disconnect()` cycle works
  - `connect_concurrency=1` with two independent hanging clients and a deadline shorter than both timeouts: the one
    waiting for the slot when the deadline passes is skipped (`None`), not `timed_out`
  - the rollback of a failed `connect()` is bounded too: a client that hangs in `disconnect()` plus a client that
    fails to connect, `disconnect_deadline=0.05`: `connect()` raises `ConnectError` within a short bound and the
    hanging client is `timed_out`
  - a cancellation of `disconnect()` from outside still propagates (existing tests keep passing)
- [ ] in `_disconnect_client()`, after taking the slot, if `remaining <= 0` record the client as skipped (append to a
      `skipped` list passed in like `disconnected`) and return normally without entering `_measure`, so its timing
      keeps `None` and its own dependencies are released and skipped in turn
- [ ] after `_in_dependency_order()` returns in `_disconnect_clients()`, if `skipped` is not empty log one ERROR record
      with the count and the names in disconnect order (`extra=fields()`); the `DEBUG` progress records stay as they are
- [ ] update the `ClientTiming` docstring in `src/nuke_di/timings.py`: a client skipped after the disconnect deadline
      keeps `None` for `disconnect`
- [ ] run `make test` - must pass before task 4

### Task 4: Verify acceptance criteria
- [ ] verify all requirements from Overview are implemented: default `20`, env var, validation, shared deadline for
      `disconnect()` and the rollback, `timed_out` for the running clients, `None` plus one log record for the skipped
- [ ] verify edge cases: `0`, `inf` (`DISCONNECT_DEADLINE_SECONDS=inf` disables the bound), deadline larger than
      `timeout × chain`, `CONNECT_CONCURRENCY` waiting, outside cancellation, reconnect after an expired deadline
- [ ] run the full suite `make test` and `make examples`
- [ ] run `make lint` (ruff, ruff format, mypy, pyright) - all issues must be fixed
- [ ] run `make cov` and check the new code paths in `core.py` and `options.py` are covered

### Task 5: [Final] Update documentation, changelog and version
- [ ] `docs/guide/configuration.md`: add the `DISCONNECT_DEADLINE_SECONDS` row (`20`, the budget of a whole
      `disconnect()`, seconds: the clients still disconnecting when it runs out are cut off, the ones not started are
      skipped) and `disconnect_deadline=` to the explicit settings example
- [ ] `docs/guide/clients.md` (~L81): each `disconnect()` by `DISCONNECT_TIMEOUT_SECONDS` and the whole disconnect by
      `DISCONNECT_DEADLINE_SECONDS`; in the timings section, a client skipped after the deadline keeps `None`
- [ ] replace `SHUTDOWN_GRACE_SECONDS + DISCONNECT_TIMEOUT_SECONDS × the longest chain of dependencies` with
      `SHUTDOWN_GRACE_SECONDS + DISCONNECT_DEADLINE_SECONDS` in `docs/guide/workers-and-jobs.md` (grace period and
      the Kubernetes manifest comment), `docs/guide/servers-in-workers.md`; reword `docs/guide/taskiq.md`
      (`--shutdown-timeout` above `DISCONNECT_DEADLINE_SECONDS`); say that 10 + 20 equals the Kubernetes default of 30,
      so leave the pod a margin above it or lower one of the two
- [ ] `examples/service_layout/k8s.yaml` and `examples/service_layout/README.md`: recompute the budgets with the
      deadline (set `DISCONNECT_DEADLINE_SECONDS` in the ConfigMap so `10 + deadline` and `5 + deadline` stay below 30)
- [ ] add `docs/adr/0012-disconnect-bounded-by-one-deadline.md`: why a second setting instead of redefining
      `DISCONNECT_TIMEOUT_SECONDS` (one hung client must not consume the budget of the clients after it; no breaking
      change), why the shared deadline is checked per client instead of one `asyncio.timeout` around the DAG
      (a `CancelledError` from the TaskGroup would be indistinguishable from an outside cancellation and record
      `cancelled`), why skipped clients keep `None` instead of a new `Outcome` value
- [ ] `CHANGELOG.md`: a `## [1.19.0] - <date>` section, **Added** for `DISCONNECT_DEADLINE_SECONDS` /
      `disconnect_deadline`, **Changed** for the default bound of 20 s (a tree whose chain of hung disconnects took
      longer is now cut off), with the compare link at the bottom; `uv version --bump minor`; `make check-version`
- [ ] run `uv run python scripts/llms.py` to regenerate `llms.txt` / `llms-full.txt`, then `make test`
      (`tests/test_docs.py`, `tests/test_llms.py`)

*Note: ralphex automatically moves completed plans to `docs/plans/completed/`*

## Technical Details
- Setting: `DependenciesSettings.disconnect_deadline: float`, env `DISCONNECT_DEADLINE_SECONDS`, default `20.0`,
  `>= 0`; `0` skips every client, `inf` disables the bound.
- Flow of `_disconnect_clients(clients)`:
  1. `deadline = loop.time() + settings.disconnect_deadline`, `skipped: list[NotSingletonClient] = []`
  2. `_in_dependency_order(clients, consumers, partial(_disconnect_client, limiter=..., disconnected=..., total=...,
     deadline=deadline, skipped=skipped))`
  3. per client, after the limiter slot: `remaining = deadline - loop.time()`; `remaining <= 0` → append to `skipped`,
     return; otherwise `bound = min(disconnect_timeout, remaining)` and
     `with _measure(...): await _within(bound, client.disconnect())`; on `TimeoutError` log the message of whichever
     bound was smaller
  4. after the order: one ERROR record for `skipped`; `finally: self.flush()` as now
- Outcomes: running at the deadline → `timed_out`; not started → `None` (no new `Outcome` literal); client errors and
  outside cancellation unchanged.
- Cost: one `loop.time()`, one subtraction and one `min()` per client; no extra timer, no extra task.
- Worst case of a process stop: `SHUTDOWN_GRACE_SECONDS + min(DISCONNECT_DEADLINE_SECONDS,
  DISCONNECT_TIMEOUT_SECONDS × the longest chain)`; documented as `SHUTDOWN_GRACE_SECONDS + DISCONNECT_DEADLINE_SECONDS`.
- Version: minor, `1.18.5` → `1.19.0`.

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

- Three parallel reviews of the branch (`code-review:code-review`, `superpowers:requesting-code-review`,
  `mattpocock-skills:code-review`), findings processed with `superpowers:receiving-code-review`.
- Push the feature branch and open a PR with `Closes #31`; drop the `needs-triage` label on the issue. The issue is
  labelled `breaking`, but with a new setting the change is not breaking: relabel or explain in the PR.
- Merging the PR publishes 1.19.0 to PyPI: it needs the maintainer's go-ahead.
