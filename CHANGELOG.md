# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.14.1] - 2026-10-09

### Documentation

- Coding agents get the model of `nuke-di` instead of guessing it from other DI libraries (#106). A new guide
  page, `docs/guide/agents.md`, in every language:
  - An Agent Skill, `skills/nuke-di/SKILL.md`: the model, the recipes for the common tasks, a table of what not
    to write (provider functions, interface binding, per-request clients, retries in `connect()`) with the ADR of
    each, and how to check the result. The repository is a Claude Code plugin marketplace
    (`/plugin marketplace add troyan-dy/nuke-di`, then `/plugin install nuke-di@nuke-di`); Codex, Cursor,
    Copilot and Gemini CLI read the same file from `.agents/skills/`.
  - A block to paste into a project's `AGENTS.md` or `CLAUDE.md`, for agents without skills.
  - `llms.txt` and `llms-full.txt` at the root, generated from the English README and guide by
    `scripts/llms.py`; `tests/test_llms.py` fails when they drift from the pages.
  - `context7.json`, so the Context7 MCP server indexes the README, the guide and the examples.
  - The graph as JSON: a recipe over `Graph.nodes`, not a new export.
- `benchmarks/agents/`: coding tasks that an agent solves in a fresh project, with and without the skill, graded
  by pytest, hidden tests, mypy with the plugin and a search for the rejected designs. With Claude Sonnet, six tasks
  three times each: 12/18 without the skill, 18/18 with it and 18/18 with the `AGENTS.md` block; the skill also
  halves the cost and the turns of a run. Without either, the agent retried in `connect()`, created an
  `httpx.AsyncClient` in `__init__` and typed a client's argument with a `Protocol`.
## [1.14.0] - 2026-10-09

### Added

- The integration kit is public: `nuke_di.integration` (#66), with `Framework`, `DependsFramework`, `Binding`,
  `bind()`, `client_of()`, `running()`, `wrap_lifespan()` and `unique()`. The FastAPI, FastStream and Litestar
  integrations are built on it, so an integration with another framework can live in its own package.
- `nuke_di.integration.testing.check(framework, make_app, run)`: the contract every integration keeps, as one
  call for the integration's own tests. It brings its own clients and handlers and checks that a handler takes
  clients by type hint, and a dependency too with a `DependsFramework`, that `override()` before startup applies, that a handler called
  without the app's lifespan raises the framework's "not connected" error, and that a failed `connect()` fails
  the startup with a `RuntimeError` and leaves the container flushed. Failed cases come back as one
  `ExceptionGroup`, each with a note naming its case. The FastAPI, FastStream and Litestar integrations run it.
- A guide page, "Writing an integration" (`docs/guide/integrations.md`), mirrored in the six translations: the
  kit, the FastStream integration written on it as the worked example, what `per_container` means, frameworks
  without `Depends`, and `check()` with its real output. The README "Documentation" section links to it.

### Deprecated

- `nuke_di._integration` imports with a `DeprecationWarning` and is removed in 1.15.0; import from
  `nuke_di.integration`.

## [1.13.0] - 2026-10-09

### Changed

- Clients connect by their own dependencies instead of layer by layer
  ([#28](https://github.com/troyan-dy/nuke-di/issues/28), ADR-0007): a client starts as soon as the clients it
  declares in `__init__` have connected, concurrently with every other client that is ready, and disconnects as
  soon as the clients that depend on it have, whatever their `disconnect()` ended in. A slow client holds back
  only the clients that need it: the example in the Clients guide (Postgres 0.3 s; Consumer 0.3 s, which needs
  only the 0.05 s Kafka) starts in 0.35 s instead of 0.60 s, and one hung `disconnect()` no longer stalls the
  clients beside it. Only declared dependencies are ordered, as before: a client that relied on an unrelated
  client of the layer below being connected first has to declare it now.
- The first failure in `connect()` cancels every client still connecting, across the whole graph; the clients
  still waiting for their dependencies keep `None` in their `ClientTiming`, and the connected ones are rolled
  back as before.
- A client whose `connect()` ends in a `CancelledError` of its own, e.g. re-raised from a task it awaited, fails
  the connect with a `ConnectError` like an exception does; its consumers would otherwise wait for it forever. Up
  to 1.12 it was skipped as not connected and the layers above it connected anyway. Its `connect_outcome` is
  `"failed"`, which tells it apart from the clients cancelled because of it, and the message has no empty `: `
  after an exception without one. A `CancelledError` of its own in `disconnect()` is logged now, like a failure.
- `CONNECT_CONCURRENCY` counts only clients in `connect()` or `disconnect()`; a client waiting for its
  dependencies does not take a slot.
- The `DEBUG` records of a client connecting or disconnecting say how many have finished so far:
  `Connecting client Consumer (2/7 connected)`, `Disconnected client Http in 0.000s (4/7 disconnected)`.
  The startup summary drops the layers: `Connected 7 clients in 0.35s (slowest: ...)`.
- `deps.timings` and `Run.clients` list the clients in resolution order, a client after its dependencies, instead
  of layer by layer.
- The Clients guide section "Layers" is "Connect order" now, with an animated SVG of the example tree connected
  three ways: one after another as in 1.0 (0.90 s), by layer as up to 1.12 (0.60 s) and by own dependencies
  (0.35 s). The README shows how a startup goes now with a second animation, `docs/connect-now.svg`, in every
  language.
- The benchmark scenarios are renamed: `wide: N independent clients`, `deep: a chain of N`, and the application
  rows `ideal: the critical path` and `above the critical path`.

### Removed

- Layers: `ClientTiming.layer`, `Node.layer`, the `layer` field of log records, the `Connecting layer N` debug
  record and the layer subgraphs of `Graph.to_mermaid()`, which now draws the clients and the arrows alone. Removing
  public fields in a minor release departs from semantic versioning on purpose: the fields described a schedule
  that no longer exists (ADR-0007).
- `ClientTiming` takes everything after `name` by keyword only, so `ClientTiming("Orders", 1)`, which set the
  layer up to 1.12, raises a `TypeError` instead of setting `connect` silently.

## [1.12.1] - 2026-10-09

### Documentation

- ADRs for the designs rejected so far that had none: ADR-0008, a client depends on concrete clients, with no
  binding of a Protocol or an ABC to an implementation and no qualifiers or multibinding (#9); ADR-0009,
  `connect()` is fail-fast and restarting is the orchestrator's job (#11); ADR-0010, the library stays pure
  Python, a Rust or compiled core was measured and is not worth it (#89, #16). AGENTS.md points to them.

## [1.12.0] - 2026-10-09

### Added

- A mypy plugin, `plugins = ["nuke_di.mypy"]` (#52). At every `resolve()`, `inject()`, `@job` and `@worker` it
  walks the `__init__` of every client the call would build and reports what the container would raise, with the
  same message: an argument without a type hint, a type that is not a client, an optional client, a positional-only
  client and a cycle, with the path from the call (`resolving settle -> Orders -> Payments -> Orders`), all the
  errors of a tree at once, under the error code `nuke-di`. It also types the result of `inject()` as the function
  without its client arguments, `def (user_id: int) -> Coroutine[Any, Any, str]` instead of
  `Callable[..., Coroutine[Any, Any, str]]`; an argument after a client becomes keyword-only, as with the `partial`
  that `inject()` returns. The plugin reads only what mypy keeps in its cache, so a warm cache reports the same as a
  cold one, and a change to a client deep in a tree checks the calls of that tree again (the
  `get_additional_indirect_deps` hook of mypy 2.4, the checker's module references before; the `dmypy` daemon
  may miss such a change until it restarts). Works with mypy 1.13 and later; Pyright has no plugin API. A client
  whose type mypy sees differently from the container is read as the container reads it: a generic client with its
  parameters, `Repository[int]`, and an alias of a `type` statement are no client, a `__db` argument is not
  positional-only. See "Checking the tree with mypy" in `docs/guide/clients.md`.
- The repository's own `mypy` runs with the plugin: it reports exactly the intended errors of the error tests and
  of the `startup_failures` and `testing` examples, which carry `# type: ignore[nuke-di]`, and nothing else.

### Changed

- `job(hooks=...)` and `worker(hooks=...)` are typed as returning `EntrypointDecorator`, a protocol whose
  `__call__` keeps the type of the decorated function as `Callable[[F], F]` did: applying it is a call the plugin
  sees.
## [1.11.5] - 2026-10-09

### Documentation

- ADR-0006: a client lives as long as its container. Per-request or per-message clients are not added
  (#65 rejected): a transaction or anything else that lives for one request is opened in the handler
  through a method of a client. `NotSingletonClient` is a workaround slated for removal in a future major
  version; new code does not build on it. The decision is noted in CONTEXT.md, AGENTS.md, the FastAPI
  spec and the Clients guide page with its translations.

## [1.11.4] - 2026-10-09

### Added

- `examples/`: 21 runnable scenarios, each a package with a short README, the real output of its run and
  tests. One-off scripts and `@job`s with parameters, workers that consume a SQLite task queue and NATS
  (nats-py in a queue group), a periodic worker, background tasks, FastAPI, Litestar, FastStream on NATS
  and Starlette as a framework without an integration, settings from the environment, `NotSingletonClient`,
  dataclass clients, the graph, startup failures, hooks with metrics and JSON logs, a cookbook of tests and
  a service with an API, an outbox worker, a cleanup job and its Kubernetes manifests. They run from
  `examples/` (`uv run python -m hello.main`); `make examples` runs their tests, and the README "Documentation" section and its
  translations link to the index.
- `tests/test_examples.py` runs the tests of every example and its programs, workers stopped by a signal
  included, so an example that drifts from the library fails CI. The CI test job starts a NATS service for
  the NATS examples, which are skipped where no server listens on localhost:4222. mypy and pyright check
  `examples/` too.

## [1.11.3] - 2026-10-09

### Changed

- The README is a landing page now: the pitch, the Quick start, the principles, the comparison with other DI
  libraries, a job with command-line arguments and a FastAPI app, each with its real output. The full
  documentation moved, unchanged, into a guide of one page per topic in `docs/guide/` (clients, the container,
  workers and jobs, FastAPI, Litestar, FastStream, testing, configuration, errors, development), linked from the
  README "Documentation" section and translated into the same six languages in `docs/i18n/<language>/`. The
  example run of `benchmarks/run.py` moved into `docs/benchmarks.md`. The README links to the guide by
  absolute URLs, so they also work on PyPI. `tests/test_readme_translations.py` checks the guide pages and their
  translations as it checks the README, that a link to a section of another page lands on a heading, and that
  a translation links to the pages of its own language. The error for a client used as a pydantic type links
  to the FastAPI guide page.

## [1.11.2] - 2026-10-09

### Changed

- The benchmark baseline on Python 3.11–3.14 and the comparison with other libraries on 3.11.7 are retaken at
  1.11.1, after the performance changes of 1.9.1–1.11.1 (#29, #30, #32, #33, #35, #36) and the fix below; the
  1.9.0 figures are in the git history of `docs/benchmarks/`. A cold `resolve()` costs 3.7–6.9 µs per client
  instead of 7–14 µs, a chain of 1000 clients 4.2–5.1 ms instead of 12.3–13.8 ms, and a second container of
  the same process, what every test after the first pays, 1.1–1.7 µs per client with real or string
  annotations, where it cost the same as the first. A warm `resolve()` is 82–109 ns instead of 100–135 ns.
  `connect()` of a chain of 1000 clients on 3.11 takes 100 ms instead of 211 ms. Against the other libraries
  at 100 clients, a cold start costs 532 µs against 1.02 ms for dependency-injector, 1.38 ms for injector,
  13.2 ms for dishka and 21.0 ms for wireup; with string annotations dependency-injector is 1.2 times ahead; a
  cached root is 93 ns against 37 ns for dependency-injector and 97 ns for wireup; a FastAPI request costs the
  same as with dishka, half of wireup and dependency-injector. `docs/benchmarks.md`, the README "Performance"
  section and its translations show the new figures.
- `benchmarks/compare.py` makes the classes of the cold start for every sample, as the cold row of
  `benchmarks/run.py` does: with the same classes every sample, the per-class cache of 1.9.1 served `nuke-di`
  after the first one, so the row measured its second container, 158 µs for 100 clients instead of a cold
  about 0.5 ms (532 µs in the baseline).

### Fixed

- A warm `resolve()`, the singleton handed out again, is back to about 100 ns: the check that the class is a
  client ([#55](https://github.com/troyan-dy/nuke-di/issues/55)) runs after the cache lookup instead of before
  it, which a non-client never passes anyway. On 3.11.7 a warm `resolve()` of the same root in a loop took
  99 ns in 1.10.2, 164 ns in 1.11.0 and 1.11.1, and 99 ns now; the baseline has it at 82–109 ns on 3.11–3.14.
  An unhashable argument, `resolve({})`, still gets the "is not a client" message.

## [1.11.1] - 2026-10-09

### Changed

- `resolve()` builds a tree on a stack of frames of its own instead of a call per client of a chain, so a chain
  of any length resolves under the default recursion limit: a chain of 500 clients raised `RecursionError` on 3.11
  and one of 1000 on 3.14, and the tests resolve a chain of 2000. The path of a resolve, its cycle check and every
  error message are unchanged, a failure leaves no frame open, and a client's `__init__` that resolves from the
  same container still re-enters it. `benchmarks/run.py` no longer raises the recursion limit;
  `benchmarks/compare.py` still does, for dishka, wireup, injector and, on 3.11, dependency-injector. The saving
  is smaller than the 2–3 µs per client the issue expected: a chain of 1000 clients resolves in 3.95 ms instead
  of 4.50 ms cold and 1.36 ms instead of 1.47 ms in a second container on 3.11, 0.4–0.55 µs and 0.1–0.15 µs per
  client, and in 4.71 ms instead of 4.77 ms and 1.35 ms instead of 1.39 ms on 3.14, about 0.05 µs. On 3.11 the
  dict comprehension of the old code was a second Python frame per client of a chain, until PEP 709 inlined
  comprehensions in 3.12, which is also why the old limit was about half as long on 3.11. The wide tree of 1000
  is unchanged within a few percent either way: 4.30 against 4.14 ms cold and 1.65 against 1.60 ms in a second
  container on 3.11, 4.58 against 4.41 ms and 1.48 against 1.55 ms on 3.14 (`benchmarks/run.py --only resolve`,
  three interleaved runs of 20 repeats each) ([#35](https://github.com/troyan-dy/nuke-di/issues/35)).

## [1.11.0] - 2026-10-09

### Changed

- Every container error names the call, the state and the way out: `resolve(Cache): the container is already
  connected; resolve, inject, mock and override only work before connect(), flush() after disconnect()`,
  `connect(): already connected, call disconnect() first`, `disconnect(): already disconnected`; a timeout
  reads `Kafka did not connect within 30s (CONNECT_TIMEOUT_SECONDS)`; a failed `connect()` reads
  `Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable`; a failed `__init__` reads
  `Strict.__init__ raised ValidationError (resolving Db -> Strict): ...`, the path before a cause that may span
  lines. The
  exception classes are the same, and "already connected" / "already disconnected" stay in the text, so a test
  that matches them keeps matching; the log records of the `nuke_di.core` logger carry the same texts
  ([#54](https://github.com/troyan-dy/nuke-di/issues/54)).
- Errors, logs and `ClientTiming.name` name a client by its `__qualname__`, so a nested class reads
  `Outer.Inner`, and in full, module included (`app.orders.Database`), when the container holds two clients
  with the same name, a Replacement counted; a unique name stays short, so every message shown in the README is unchanged
  ([#64](https://github.com/troyan-dy/nuke-di/issues/64)).

### Added

- `resolve()` refuses a class that is not a client, with
  `InvalidSignatureError: Settings is not a client: subclass Client or NotSingletonClient`, instead of building
  it and failing at `connect()` with an `AttributeError` on `connect`
  ([#55](https://github.com/troyan-dy/nuke-di/issues/55)).

## [1.10.2] - 2026-10-09

### Added

- CI runs the tests that need no framework on free-threaded CPython 3.14t with `PYTHON_GIL=0`, as
  `make test-free-threaded` does ([#32](https://github.com/troyan-dy/nuke-di/issues/32)).

### Fixed

- `resolve()`, `inject()`, `mock()`, `override()` and `flush()` are serialized by one reentrant lock per
  container, so a singleton asked for by two threads at once is built once instead of twice, and a resolve in
  one thread no longer reports the path of another as a `CircularDependencyError`. The lock is not held inside
  an `override()` block, and `connect()` / `disconnect()` stay unlocked: they belong to one event loop. A
  resolved singleton is still handed out without the lock, on purpose, so the warm path costs what it did; see "The container" in
  the README ([#32](https://github.com/troyan-dy/nuke-di/issues/32)).

## [1.10.1] - 2026-10-09

### Added

- The benchmark suite measures what real code pays: `resolve(), cold` of the wide, deep and mixed trees with
  string annotations (`wide, strings` and so on), the classes `from __future__ import annotations` produces,
  which cost about twice the real-type figure (`resolve(), cold` now builds its classes afresh for every
  sample, so a per-class cache cannot serve it); `resolve(), second container, classes seen before`, a fresh
  `Dependencies()` for classes resolved earlier in the process, what every test of a session pays and the
  row a per-class cache would change; and an `application` connect shape of 8 clients whose `connect()` and
  `disconnect()` sleep for 1–60 ms, with the wall time of the container against the critical path of the
  same coroutines, so the time lost at the layer barriers shows. `benchmarks/compare.py` runs the
  string-annotation trees through every library as an extra row of the cold-start summary; the baseline
  and the comparison in `docs/benchmarks.md` are retaken
  ([#38](https://github.com/troyan-dy/nuke-di/issues/38)).

## [1.10.0] - 2026-10-09

### Changed

- The autospec Replacement of `mock(cls)` and `override(cls)` is created with `instance=True`: it stands in
  for an instance, so calling it raises `TypeError` instead of returning another mock, and the graph labels
  it `NonCallableMagicMock`. It is still an instance of `cls`, and its async methods are still `AsyncMock`s
  ([#53](https://github.com/troyan-dy/nuke-di/issues/53)).
- pyright runs in `make lint` and in CI next to mypy, from `uvx` with a pinned version, over `src/` and
  `tests/`; `[tool.pyright]` in pyproject.toml holds its settings
  ([#60](https://github.com/troyan-dy/nuke-di/issues/60)).

### Fixed

- `Dependencies.mock(cls)` and `override(cls)` without a Replacement of your own are typed `Any`, as
  `unittest.mock.create_autospec` is, so `db.fetch_user.return_value = ...` and
  `db.fetch_user.assert_awaited_once_with(...)` from the README "Testing" examples pass `mypy --strict` and
  pyright; `mock(cls, new)` and `override(cls, new)` keep the type of the class. `tests/test_typing.py` runs
  `mypy --strict` over the Python blocks of the README "Testing" section, laid out as the files they name, so
  a README edit that breaks them fails CI ([#53](https://github.com/troyan-dy/nuke-di/issues/53)).
- `Dependencies.inject(func)` keeps the return type of `func`: `await injected(42)` is a `str` for a handler
  that returns one, not `Any`. The arguments of the result stay untyped, a type checker cannot subtract the
  client arguments from a signature ([#62](https://github.com/troyan-dy/nuke-di/issues/62)).
- `client_dataclass` is typed as an identity decorator, so `resolve(Checkout)` type-checks for a decorated
  class that subclasses `Client`; a class without the base is a client at runtime only, and the README
  recommends `@client_dataclass(frozen=True) class Checkout(Client):`
  ([#60](https://github.com/troyan-dy/nuke-di/issues/60)).

## [1.9.2] - 2026-10-09

### Changed

- `connect()` and `disconnect()` await each client's coroutine under `asyncio.timeout()` instead of
  `asyncio.wait_for()`, in the connecting task itself. On Python 3.11 `wait_for()` ran every call in a task
  of its own, about 40 µs per client per phase: a chain of 1000 clients takes 104 ms instead of 184 ms, a
  layer of 1000 clients 14.2 ms instead of 17.7 ms; 3.12 and later already ran the coroutine in the caller's
  task and are unchanged. Outcomes, errors and logs are the same, and a timeout of `0` still expires before
  the client's coroutine starts ([#30](https://github.com/troyan-dy/nuke-di/issues/30)).

### Fixed

- A `disconnect()` cancelled from outside (an ASGI server tearing down the lifespan, a second signal) left
  the container with `connected=False` but its clients, layers and timings still registered, so the next
  `connect()` would have reconnected the half-disconnected instances and `resolve()` would have handed them
  out. The container is now flushed whichever way `disconnect()` ends. A client whose `disconnect()` raises
  `CancelledError` of its own, re-raising the cancellation of a task it awaited for instance, no longer stops
  `disconnect()` there: the rest of its layer and the layers below are still disconnected, and the client is
  recorded as `cancelled`. The rollback of a failed `connect()` behaves the same
  ([#37](https://github.com/troyan-dy/nuke-di/issues/37)).

## [1.9.1] - 2026-10-09

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

[Unreleased]: https://github.com/troyan-dy/nuke-di/compare/v1.14.1...HEAD
[1.14.1]: https://github.com/troyan-dy/nuke-di/compare/v1.14.0...v1.14.1
[1.14.0]: https://github.com/troyan-dy/nuke-di/compare/v1.13.0...v1.14.0
[1.13.0]: https://github.com/troyan-dy/nuke-di/compare/v1.12.1...v1.13.0
[1.12.1]: https://github.com/troyan-dy/nuke-di/compare/v1.12.0...v1.12.1
[1.12.0]: https://github.com/troyan-dy/nuke-di/compare/v1.11.5...v1.12.0
[1.11.5]: https://github.com/troyan-dy/nuke-di/compare/v1.11.4...v1.11.5
[1.11.4]: https://github.com/troyan-dy/nuke-di/compare/v1.11.3...v1.11.4
[1.11.3]: https://github.com/troyan-dy/nuke-di/compare/v1.11.2...v1.11.3
[1.11.2]: https://github.com/troyan-dy/nuke-di/compare/v1.11.1...v1.11.2
[1.11.1]: https://github.com/troyan-dy/nuke-di/compare/v1.11.0...v1.11.1
[1.11.0]: https://github.com/troyan-dy/nuke-di/compare/v1.10.2...v1.11.0
[1.10.2]: https://github.com/troyan-dy/nuke-di/compare/v1.10.1...v1.10.2
[1.10.1]: https://github.com/troyan-dy/nuke-di/compare/v1.10.0...v1.10.1
[1.10.0]: https://github.com/troyan-dy/nuke-di/compare/v1.9.2...v1.10.0
[1.9.2]: https://github.com/troyan-dy/nuke-di/compare/v1.9.1...v1.9.2
[1.9.1]: https://github.com/troyan-dy/nuke-di/compare/v1.9.0...v1.9.1
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
