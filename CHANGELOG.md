# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- The arguments of a client's `__init__` are read once per class in the process instead of on every resolve of
  every container: the result is kept on the class, keyed by the identity of the `__init__` it was read from.
  A subclass that inherits `__init__` shares the entry of its base class; one that redefines `__init__`, or a
  class whose `__init__` is replaced, is read again; mutating `__defaults__` or `__annotations__` of the same
  `__init__` in place after the first resolve is not seen. A type hint that fails to evaluate is not kept and
  fails again. A class whose `__init__` is `object.__init__` takes no arguments and is not inspected. The
  per-client debug records of `resolve()`, `connect()` and `disconnect()` are built only when the
  `nuke_di.core` logger is enabled for `DEBUG`. Together with the two changes below, cold `resolve()` of a
  tree of 100 or 1000 clients on the second container of a process went from 7.8–12.2 µs to 1.4–1.7 µs per
  client on CPython 3.14 ([#29](https://github.com/troyan-dy/nuke-di/issues/29)).
- The parameters of a plain `__init__` are read from its code object instead of `inspect.signature`, which
  stays for a decorated `__init__` (`__wrapped__`), a declared `__signature__` and a C function; the type hints
  still come from `get_type_hints`, so forward references and string annotations keep working. The first
  container of a process resolves a tree of 100 clients in about 0.43 ms instead of 0.94 ms
  ([#36](https://github.com/troyan-dy/nuke-di/issues/36)).
- The cycle check of `resolve()` looks a class up in a set beside the path list instead of scanning the list,
  which was as long as the depth of the resolution, about 30% of a chain of 1000 clients before: the chain
  now costs the same per client as a wide tree, 1.45 µs against 1.43 µs
  ([#33](https://github.com/troyan-dy/nuke-di/issues/33)).

## [1.9.0] - 2026-10-09

### Added

- `Dependencies.graph()` returns a `Graph` of the resolved clients: one `Node` per client in resolution order
  with its class, whether it is a singleton, its layer, the Replacement standing in for it and its dependencies
  by `__init__` argument name. `Graph.to_mermaid()` renders it as a Mermaid flowchart with the layers as
  subgraphs, which GitHub draws in a README, a pull request or an issue. `Graph` and `Node` are exported from
  `nuke_di`; see "The graph" in the README ([#13](https://github.com/troyan-dy/nuke-di/issues/13)).
- A recipe in "Testing" that checks the wiring of every entrypoint in CI with `Dependencies().inject()`,
  in place of the `NUKE_DI_CHECK` mode proposed in #13: the test gives the same result without a second way
  to start a process.

## [1.8.0] - 2026-10-08

### Added

- Benchmark suite in `benchmarks/`, measurement only: `uv run python benchmarks/run.py [--size N] [--repeat K]
  [--only SCENARIO] [--json PATH]` times `resolve()` of wide, deep and mixed trees of 10, 100 and 1000 no-op
  clients, cold and warm, the scheduling overhead of `connect()` / `disconnect()` above the clients' own
  coroutines, `inject()` and the call of an injected function, `NotSingletonClient` against a singleton,
  the `flush()` + `mock()` and `override()` cycle of a test, one FastAPI request through nuke-di against a
  plain `Depends()`, the import time of `nuke_di` and `nuke_di.fastapi` and the memory of a resolved tree,
  and prints a Markdown table with the median, the p95 and the figure per client. `docs/benchmarks.md`
  records the baseline on Python 3.11, 3.12, 3.13 and 3.14, with the JSON in `docs/benchmarks/`; CI runs
  the suite as a non-blocking smoke test; see "Performance" in the README
  ([#25](https://github.com/troyan-dy/nuke-di/issues/25)).
- `benchmarks/compare.py` runs the same trees through dishka, wireup, dependency-injector and injector:
  a cold container with its registration and the root, the root again, and one FastAPI request to a
  handler that takes a client through each library's integration. The libraries are the `compare`
  dependency group, installed with the dev dependencies. `--summary` prints the three figures that answer
  which one is faster, the best per row in bold with the ratios, and `benchmarks/chart.py` draws them
  as `docs/benchmarks/compare.png`; the figures are in `docs/benchmarks.md`.

## [1.7.1] - 2026-10-08

### Added

- `docs/adr/0005-third-party-objects-as-client-classes.md`: third-party objects are wrapped in `Client`
  subclasses; provider functions and built-in connectors
  ([#8](https://github.com/troyan-dy/nuke-di/issues/8)) are rejected, with the design principle behind
  it, also stated in AGENTS.md: keep the apparent simplicity, a feature reachable in a simpler existing
  way is not added.

## [1.7.0] - 2026-10-08

### Added

- Litestar integration, `nuke_di.litestar` (`pip install "nuke-di[litestar]"`, Litestar 2.15 or newer):
  `Litestar(..., plugins=[ClientPlugin()])` lets route handlers, `@websocket` handlers, controllers and
  their dependencies take clients by plain type hints, and connects the clients on startup. Litestar
  provides dependencies by name, so one argument name means one client in the app. See "Litestar" in
  the README, `docs/specs/litestar.md` and `docs/adr/0004-litestar-clients-by-name.md`
  ([#19](https://github.com/troyan-dy/nuke-di/issues/19)).
- FastStream integration, `nuke_di.faststream` (`pip install "nuke-di[faststream]"`, FastStream 0.6 or
  newer, any broker): after `setup(app)`, subscribers and their `Depends(...)` take clients by plain type
  hints; the clients connect before the brokers start and disconnect after they stop. A test starts the
  app with `TestApp` inside the test broker. See "FastStream" in the README and
  `docs/specs/faststream.md` ([#19](https://github.com/troyan-dy/nuke-di/issues/19)).
- FastAPI websocket endpoints and their dependencies take clients by type hint, on the app given to
  `setup()` and on a `ClientRouter` ([#19](https://github.com/troyan-dy/nuke-di/issues/19)).

## [1.6.0] - 2026-10-08

### Added

- Startup timings ([#14](https://github.com/troyan-dy/nuke-di/issues/14)): the container measures every client's
  `connect()` and `disconnect()`, not counting the wait for `CONNECT_CONCURRENCY`. `Dependencies.timings` holds one
  `ClientTiming` per client of the last `connect()` (name, layer, durations, and an outcome per phase: `"ok"`,
  `"failed"`, `"timed_out"` or `"cancelled"`) and outlives `disconnect()`; a worker or a job passes the same list to
  hooks as `Run.clients`.
- An `INFO` summary after a successful connect, e.g. `Connected 12 clients in 3 layers in 1.84s (slowest: Kafka
  1.52s, Postgres 0.21s, Redis 0.05s)`, and a `WARNING` for every client that used more than half of
  `CONNECT_TIMEOUT_SECONDS`. `DEBUG` records of a connected or disconnected client now carry its duration.
- Structured fields on every log record of `nuke_di`, for `logging`'s `extra=`: `client`, `layer`, `duration` and,
  on every record made inside a worker or a job (the container's and background tasks' included), `run`.

### Changed

- `import nuke_di` no longer imports `unittest` (now imported by `mock()`) or `argparse` (now imported when an
  entrypoint runs): about 5 ms less at the start of every process. `Option` moved to `nuke_di.options`; it is still
  exported from `nuke_di` and `nuke_di.cli`.

## [1.5.2] - 2026-10-08

### Added

- The README in Russian, Simplified Chinese, Spanish, Brazilian Portuguese, Japanese and Polish
  (`docs/i18n/README.<language>.md`), with a language switcher at the top of every README. Code examples and their
  output are not translated: a test checks that every translation shows exactly the code blocks of
  `README.md`, the same sections and links, and that the links resolve. The source package includes the translations.
- PyPI keywords and classifiers for search: `Framework :: FastAPI`, `Framework :: Pytest` (the pytest
  plugin list picks it up), application framework, workers, background jobs, graceful shutdown.

The code of the package is unchanged; its metadata and PyPI description are new.

## [1.5.1] - 2026-10-08

### Changed

- Every merge into `master` is a release: the `Release` workflow publishes the version in `pyproject.toml`
  to PyPI, tags it and creates a GitHub release with its CHANGELOG section. A pull request into `master`
  fails CI unless it raises the version and adds its CHANGELOG section and compare link; a change that
  reaches `master` without a new version fails the `Release` workflow. `make check-version` runs the same
  check locally. The package itself is unchanged.

## [1.5.0] - 2026-10-08

### Added

- FastAPI integration, `nuke_di.fastapi` (`pip install "nuke-di[fastapi]"`, FastAPI 0.105 or newer): path
  operations and their dependency functions take clients by plain type hints, e.g.
  `async def get_user(user_id: int, users: UserService)`; classes used as dependencies too. `setup(app)`
  connects the clients of the routes the app serves on startup and disconnects them on shutdown;
  `ClientRouter` is an `APIRouter` whose routes take clients the same way. A test replaces a client with
  `override()` before `TestClient` starts the app. See "FastAPI" in the README,
  `docs/specs/fastapi.md` and `docs/adr/0003-fastapi-signature-rewrite.md`
  ([#12](https://github.com/troyan-dy/nuke-di/issues/12)).
- A client used where pydantic expects a field type, e.g. in a router without `ClientRouter`, raises a
  `TypeError` that names the fix instead of pydantic's schema error. Models with `arbitrary_types_allowed`
  are unaffected.

## [1.4.0] - 2026-10-08

### Added

- `CircularDependencyError`, a subclass of `InvalidSignatureError`, names the cycle:
  `Circular dependency: Orders -> Payments -> Orders`
  ([#6](https://github.com/troyan-dy/nuke-di/issues/6)).

### Changed

- Resolution checks a client's `__init__` before calling it. A required argument without a type hint,
  of a type that is not a client (e.g. a `Protocol`), of an optional client (`Client | None`), or a
  positional-only client now raises `InvalidSignatureError` naming the argument and the resolution path,
  e.g. `(resolving Checkout -> Profiles)`. Before, `__init__` was called anyway and failed with an
  `InitializeDependencyError` wrapping `TypeError: missing 1 required positional argument`.
- A type hint that cannot be evaluated, in a client's `__init__` or in a function given to `inject()`,
  raises `InvalidSignatureError` instead of a bare `NameError`.
- These signatures failed before too, only with another exception. Code that caught
  `InitializeDependencyError` or `NameError` for them should catch `InvalidSignatureError`; in a worker or
  a job the exit code stays `1`.

### Fixed

- Clients that depend on each other in a cycle raised `RecursionError` with a thousand-frame traceback.

## [1.3.0] - 2026-10-08

### Added

- `Dependencies.override(cls, new=None)`: a context manager that registers a Replacement for `cls` until the
  end of the `with` block, across any number of connect / disconnect cycles, and leaves the container
  flushed. It needs a container without resolved clients on entry. Works with the global `DI`. See
  "Testing" in the README ([#7](https://github.com/troyan-dy/nuke-di/issues/7)).
- A pytest plugin, registered through the `pytest11` entry point, with the `di` (a fresh container) and
  `global_di` (the global `DI`, flushed before and after the test) fixtures. A test that leaves its
  container connected gets an error at teardown. Nothing is autouse. Turn it off with
  `pytest -p no:nuke_di`.

### Fixed

- `mock(cls)` after `cls` was resolved silently returned the real client, so a test ran against it while
  holding what it took for a mock. It now raises `ConnectError`: register Replacements before
  `resolve()` / `inject()`. Code that relied on the old result gets an error instead of a false pass.
- `mock(cls, new)` silently ignored `new` when `cls` already had a Replacement; it now raises
  `ConnectError`. Calling `mock(cls)` again still returns the registered Replacement.

## [1.2.0] - 2026-10-08

### Added

- Parameters: every annotated argument of a `@job` or `@worker` function that is not a client is filled
  from a command-line option, e.g. `python -m app.jobs.sync --date 2026-10-01`. Supported types are
  `str`, `int`, `float`, `Path`, `bool`, `date`, `datetime`, enums, `list[...]` of them and `... | None`
  ([#3](https://github.com/troyan-dy/nuke-di/issues/3)). See "Parameters" in the README and
  `docs/specs/entrypoint-parameters.md`.
- `Option` to give a parameter a help text and a one-letter alias through `Annotated`.
- `UsageError`: an invalid command line fails the run with exit code `2` before any client is resolved.

### Changed

- A worker or a job no longer ignores its command line: `--help` prints the generated help instead of
  running, and an unknown argument fails the run with exit code `2`.

## [1.1.0] - 2026-10-08

### Changed

- Clients connect concurrently in layers, from the deepest dependencies up, instead of one by one
  in resolution order. A client still connects only after its own dependencies, but there is no
  ordering between unrelated clients anymore: declare a dependency in `__init__` if you need one.
  `disconnect()` runs the layers in reverse, concurrently within a layer.
- A failed `connect()` cancels the clients still connecting in the same layer;
  the next layers never start. See `docs/adr/0001-layered-concurrent-connect.md`.
- Every client's `disconnect()` is bounded by `DISCONNECT_TIMEOUT_SECONDS` (default `10`). A hanging client
  is logged and the others still disconnect; before, it blocked shutdown forever.

### Fixed

- A failed or cancelled `connect()` disconnects the clients that already connected, layers in reverse,
  and leaves the container disconnected and empty. Before, they stayed connected and the container
  stayed in the connected state ([#1](https://github.com/troyan-dy/nuke-di/issues/1)).
- `inject()` now raises `InvalidSignatureError` for a function argument without a type hint.
  The check never fired before, so such functions were accepted silently.
  Unannotated `*args` / `**kwargs` are still allowed.

### Added

- `@job` and `@worker` turn an async function into the main program of a process run with `python -m`:
  the clients are injected and connected, the function runs, the clients disconnect and the process
  exits with `0`, `1` or `128 + signum`. See "Workers and jobs" in the README and
  `docs/adr/0002-entrypoint-runs-on-decoration.md`.
- `Shutdown` client, set on the first SIGTERM / SIGINT; the entrypoint is cancelled after
  `SHUTDOWN_GRACE_SECONDS` (default `10`) or on a second signal.
- `BackgroundTasks` client that supervises background tasks; a failing task fails the worker or job.
- Run hooks (`RunHook`, `Run`) to observe every run, e.g. for metrics or tracing.
- `CONNECT_CONCURRENCY` setting (`DependenciesSettings.connect_concurrency`) caps how many clients
  connect or disconnect at once; `0` (the default) means no limit.
- Line and branch coverage is 100% and enforced in CI (`fail_under = 100`).

## [1.0.0] - 2026-10-08

First public release, extracted from the `nuke.di` package of the nuke framework.

### Added

- `Client` / `NotSingletonClient` base classes with async `connect()` / `disconnect()` hooks.
- `Dependencies` container and the global `DI` instance: `resolve`, `inject`, `mock`, `flush`,
  `connect` / `disconnect` and `async with` support.
- `client_dataclass` decorator.
- `CONNECT_TIMEOUT_SECONDS` setting and `DependenciesSettings`.

### Changed compared to `nuke.di`

- No runtime dependencies: settings no longer use `pydantic-settings`, logging uses the standard
  `logging` module under the `nuke_di` logger.
- Clients no longer get a per-class `_logger` attribute.

[Unreleased]: https://github.com/troyan-dy/nuke-di/compare/v1.9.0...HEAD
[1.9.0]: https://github.com/troyan-dy/nuke-di/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/troyan-dy/nuke-di/compare/v1.7.1...v1.8.0
[1.7.1]: https://github.com/troyan-dy/nuke-di/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/troyan-dy/nuke-di/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/troyan-dy/nuke-di/compare/v1.5.2...v1.6.0
[1.5.2]: https://github.com/troyan-dy/nuke-di/compare/v1.5.1...v1.5.2
[1.5.1]: https://github.com/troyan-dy/nuke-di/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/troyan-dy/nuke-di/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/troyan-dy/nuke-di/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/troyan-dy/nuke-di/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/troyan-dy/nuke-di/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/troyan-dy/nuke-di/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/troyan-dy/nuke-di/releases/tag/v1.0.0
