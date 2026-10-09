# Benchmarks

What `nuke-di` itself costs, measured on no-op clients (empty `connect()` and `disconnect()`), so the
figures are the library's and not the fakes'. This is measurement only: a reproducible suite and a
baseline for every supported Python version, so a change on the hot path shows up as a number, not as a
feeling. Optimizing anything is a separate decision, taken from these numbers
([#25](https://github.com/troyan-dy/nuke-di/issues/25)).

## Running

```console
$ uv run python benchmarks/run.py [--size N]... [--repeat K] [--only SCENARIO]... [--json PATH]
```

| Flag              | Default            | Meaning |
|-------------------|--------------------|---------|
| `--size N`        | `10`, `100`, `1000` | The number of clients in a tree; repeatable |
| `--repeat K`      | `20`               | Samples per figure, after one untimed warm-up |
| `--only SCENARIO` | all                | `resolve`, `connect`, `inject`, `not_singleton`, `overrides`, `fastapi`, `import`, `memory`; repeatable |
| `--json PATH`     |                    | Also write the figures, with every sample, the Python version, platform and commit, as JSON |

The suite needs only the standard library and `nuke-di`; the `fastapi` scenario needs `fastapi` and
`httpx` (in the dev dependencies) and is skipped without them. `make bench` runs the suite on the
project's Python, `make bench-all` on every supported version into `docs/benchmarks/py<version>.json`,
which is where the baseline below comes from. CI runs `--size 10 --repeat 1` on every pull request as a
non-blocking smoke test, so the suite keeps working; there is no regression gate, a GitHub runner is too
noisy for one.

`benchmarks/compare.py` takes the same flags, plus `--summary`, and runs `nuke-di` against other libraries, see
[Comparison with other libraries](#comparison-with-other-libraries); its scenarios are `cold`, `warm`
and `request`, and the libraries are the `compare` dependency group, which `uv sync` installs with the
dev dependencies:

```console
$ uv run python benchmarks/compare.py [--size N]... [--repeat K] [--only SCENARIO]... [--json PATH] [--summary]
```

## What is measured

Every tree is built from classes made with `dataclasses.make_dataclass`, so each client has a real
`__init__` with type hints, as a user would write it. `N` is the number of clients in the tree. The
`strings` variant of a tree has the same classes with the names of the dependencies as strings in the
type hints, which is what `from __future__ import annotations` makes of every annotation and what most
code bases have: `get_type_hints()` then compiles and evaluates every string in the globals of the class's
module, and the classes live in a module of their own, registered in `sys.modules` as a real one is. The
future import also turns the `-> None` of `__init__` into the string `'None'`, which the `strings` trees
leave as the object, so real code evaluates one more forward reference per class than they do: a slight
underestimate.

| Scenario | Shape | The figure |
|----------|-------|------------|
| `resolve(), cold` | `wide`: one root that declares `N - 1` clients without dependencies, two layers | `resolve()` of the root on a fresh `Dependencies()`, the classes never resolved before: a fresh tree per sample, so nothing a process keeps per class serves them |
| | `deep`: a chain of `N` clients, `N` layers | |
| | `mixed`: a pyramid 1, 2, 4, ... wide from the top, every client depends on two or three of the level below, about log2(N) layers | |
| | `wide, strings`, `deep, strings`, `mixed, strings`: the same trees with string annotations | |
| `resolve(), second container, classes seen before` | the same six trees | `resolve()` of the root on a fresh `Dependencies()` after the classes were resolved once in the process: what every test of a session pays after the first, and the row the per-class cache of 1.9.1 ([#29](https://github.com/troyan-dy/nuke-di/issues/29)) moved |
| `resolve(), warm` | the three trees with real type hints | A second `resolve()` of the same root: the singleton cache hit, independent of `N` and of the hints |
| `connect() + disconnect()` | `wide`: `N` clients in one layer, connected concurrently; `deep`: `N` layers, connected one after another | One `connect()` and `disconnect()` of the container |
| `..., ideal` | the same | The same clients' `connect()` and `disconnect()` coroutines awaited directly, without the container |
| `..., overhead above the ideal` | the same | The difference of the two, sample by sample: what the scheduling costs |
| `connect() + disconnect(), wall time` | `application`: 8 clients whose `connect()` and `disconnect()` sleep for the time a real connection takes, 1–60 ms, in the tree below, which has slack between its branches | The wall time of one `connect()` and `disconnect()` of the container: the sleeps, not the scheduling |
| `..., ideal: the critical path, no layer barriers` | the same | The same coroutines with every `connect()` started as soon as the client's dependencies are connected and every `disconnect()` as soon as its consumers are disconnected: the longest chain of the tree, what a schedule by dependency instead of by layer would give ([#28](https://github.com/troyan-dy/nuke-di/issues/28)) |
| `..., lost at the layer barriers` | the same | The difference of the two, sample by sample: what waiting for the slowest client of every layer costs |
| `inject(), clients resolved` | 2 clients | `inject()` of a function with two client arguments, the clients resolved already |
| `call of the injected function` / `... plain function` | 2 clients | One call of the `partial` that `inject()` returns, against one call of the function with the clients passed by hand |
| `resolve(), cold`, `N consumers of a Client` / `... NotSingletonClient` | one root, `N` consumers of one `Session` | A fresh `Session` per consumer against one shared instance |
| `flush() + mock() + resolve()` | `mixed`, a leaf replaced | The cycle of a test with the pytest plugin: `flush()`, `mock()` (an autospec mock), `resolve()` of the root |
| `override() block + resolve()` | `mixed`, a leaf replaced | `with override(Leaf, fake): resolve(root)`, the block flushes on exit |
| `one request` | FastAPI, via the `httpx` ASGI transport | One `GET` to a handler that takes a client through `nuke_di.fastapi`, to one with a plain async `Depends()`, and to one without dependencies |
| `import, fresh interpreter` | `nuke_di`, `nuke_di.fastapi`, `fastapi` | The cumulative import time from `python -X importtime` in a new process |
| `resolve(), tracemalloc peak` | `mixed`, the largest `N` | The peak traced memory of `resolve()` on a fresh container |

The application tree, with the `connect()` / `disconnect()` sleep of every client in milliseconds:
`Settings` 1 / 0; `Postgres(Settings)` 30 / 5, `Redis(Settings)` 20 / 2 and `Kafka(Settings)` 60 / 10;
`Repository(Postgres)` 15 / 1; `Users(Repository, Redis)` 10 / 1; `Consumer(Kafka)` 5 / 5;
`Api(Users, Consumer)` 2 / 1. The container connects it in five layers, each waiting for its slowest
client: `Settings`, then the three connections together, which is `Kafka`'s 60 ms, then `Repository` and
`Consumer`, then `Users`, then `Api`: 1 + 60 + 15 + 10 + 2 = 88 ms, and disconnects in reverse in
1 + 1 + 5 + 10 = 17 ms, 105 ms in all. The critical path is `Settings` → `Kafka` → `Consumer` → `Api`,
68 ms, and `Api` → `Consumer` → `Kafka` back, 16 ms: 84 ms. The 21 ms between the two is what
`Repository` and `Users` spend waiting at the layer barriers for `Kafka` while `Postgres` is long
connected.

## How to read the figures

- **Median and p95** of `K` samples; `K = 20` in the baseline. The p95 shows the noise of the machine
  rather than a property of the library: compare medians between versions, and treat a change inside the
  spread between median and p95 as nothing.
- **Per client** is the median divided by `N`, so figures at different `N` are comparable; it is left
  out where it means nothing (a cache hit, the fixed cost of an autospec mock).
- Garbage collection is paused while a sample runs, as `timeit` does, and one untimed warm-up pays the
  one-off costs, e.g. the lazy import of `unittest.mock` by `mock()`.
- `resolve()` builds a tree on a stack of frames of its own, not with a call per client of a chain, so a
  chain of any length resolves under the default recursion limit and the runner leaves the limit alone
  ([#35](https://github.com/troyan-dy/nuke-di/issues/35)). `benchmarks/compare.py` still raises it, for the
  libraries that recurse.
- The `connect()` figures include the logging calls of the container (the `nuke_di` logger with no
  handler) and a `ClientTiming` per client, which is what a real startup pays too.
- The application figures are wall time, and `asyncio.sleep()` overshoots by up to a millisecond per
  sleep in a chain, which the container and the critical path both pay: compare the two with each other,
  not either with the nominal 105 and 84 ms.

## Findings

The baseline was taken on an Apple M2 Pro, macOS 26.6.2 (arm64), `nuke-di` 1.11.1 at commit `d8dc493`,
Python 3.11.7, 3.12.5, 3.13.14 and 3.14.6, each in a fresh `uv` environment from `uv.lock`, with
`N = 10, 100, 1000` and 20 repeats. It is the state after the performance changes of 1.9.1–1.11.1: the
per-class cache of what `__init__` takes and its reader from `__code__`
([#29](https://github.com/troyan-dy/nuke-di/issues/29), [#36](https://github.com/troyan-dy/nuke-di/issues/36)),
the cycle check with a set ([#33](https://github.com/troyan-dy/nuke-di/issues/33)), `asyncio.timeout()` instead
of `asyncio.wait_for()` ([#30](https://github.com/troyan-dy/nuke-di/issues/30)), one lock per container around
`resolve()` ([#32](https://github.com/troyan-dy/nuke-di/issues/32)) and the resolve without recursion
([#35](https://github.com/troyan-dy/nuke-di/issues/35)). The "before" figures are the 1.9.0 baseline at commit
`8a414d6`, in the git history of `docs/benchmarks/`.

- **`resolve()` costs 4–6.5 µs per client** with real type hints on every version and grows linearly:
  100 clients in 0.4–0.56 ms, 1000 in 4.6–5.6 ms, against 7–14 µs per client and 8–14 ms for 1000 in
  1.9.0. The chain is no longer the most expensive shape: a chain of 1000 clients resolves in 4.6–4.9 ms
  instead of 12.3–13.8 ms, under the default recursion limit on every version. The "200 clients in about
  2 ms" of [#16](https://github.com/troyan-dy/nuke-di/issues/16) is now about 1 ms.
- **A second container costs 1.2–2.2 µs per client**, with both kinds of hints: the classes resolved once in
  the process have what their `__init__` takes cached, string annotations evaluated, so the container only
  builds the instances. That is 2.3–4.5 times below the first container with real type hints and 4.4–17
  times with strings, 0.12–0.16 ms for 100 clients and 1.3–2.2 ms for 1000; in 1.9.0 the second container
  cost the same as the first. It is what every test of a session pays after the first one.
- **String annotations cost 1.5–5.2 times the real-type figure on the first container**: 7.2–29 µs per
  client, against 11–38 µs in 1.9.0. The ratio is 1.5–2.2 on 3.11, 1.5–3.3 on 3.12, 2.5–4.1 on 3.13 and
  2.5–5.2 on 3.14, and the mixed tree pays the most, with two or three hints per class against one:
  `get_type_hints()` compiles and evaluates every string once per class, and the `annotationlib` of 3.14
  does more per string. The ratio is above that of 1.9.0 (1.3–3.5) because the cache took more off the rest
  of a cold `resolve()` than off the strings, which are evaluated once either way. Most code bases have
  `from __future__ import annotations`, so this is the figure their startup pays, and the second container
  above is the one their tests pay.
- **A warm `resolve()`, the singleton cache hit, is 150–185 ns**, up from 100–135 ns in 1.9.0: since 1.11.0
  `resolve()` checks that the class is a client before the cache lookup
  ([#55](https://github.com/troyan-dy/nuke-di/issues/55)), about 60 ns on 3.11 (99 ns in 1.10.2 against
  164 ns in 1.11.0, a warm `resolve()` of the same root in a loop). The lock of
  [#32](https://github.com/troyan-dy/nuke-di/issues/32) is not on this path: a resolved singleton is handed out
  without it.
- **`connect()` and `disconnect()` cost 10–14 µs per client in a layer of 100 or more (21–29 µs in a layer of
  10) and 0.09–0.12 ms per layer**, all of it scheduling: the clients' own coroutines take 0.16–0.4 µs each.
  3.11 is level with the other versions now: `asyncio.timeout()`
  ([#30](https://github.com/troyan-dy/nuke-di/issues/30)) took the task per client off it, and the chain of
  1000 clients connects and disconnects in 105 ms instead of 211 ms, the layer of 1000 in 11.1 ms instead of
  19.4 ms; the other versions take 98–103 ms and 10.3–11.8 ms. Not a hot spot: a real `connect()` takes
  milliseconds, a hundred to a thousand times more than its scheduling.
- **The application connects and disconnects in 110.5–110.7 ms of wall time against a critical path of
  87.0–87.1 ms**: 23.5–23.7 ms, 21%, is lost at the layer barriers, where `Repository` and `Users` wait for
  `Kafka` although `Postgres` connected 30 ms earlier. The figure is the same on every version, because
  it is the clients' sleeps and not the scheduling; it is what a schedule by dependency
  ([#28](https://github.com/troyan-dy/nuke-di/issues/28)) would recover.
- **`inject()` takes 8–10 µs** to bind a function with two clients. The `partial` it returns adds
  45–85 ns to a call that takes 37–41 ns without it.
- **A `NotSingletonClient` costs 1–1.5 µs more per consumer** than the shared instance of a `Client` at 100
  and 1000 consumers, against 5–8 µs in 1.9.0: a fresh instance is built from the cached arguments of its
  class.
- **The test cycle is `resolve()` plus the mock.** The autospec of `mock()` costs 0.7–1.2 ms, against
  1.3–1.6 ms in 1.9.0; `override()` with an instance costs the same as a `resolve()` in a second container,
  0.15–0.17 ms for 100 clients.
- **A FastAPI handler that takes a client through `nuke-di` costs the same as one with a plain
  `Depends()`**: 102–117 µs per request for both, within 4% of each other, and 5–17 µs above a handler
  without dependencies. The ASGI stack is the cost, not the injection.
- **`import nuke_di` takes 29–41 ms**, about 20 ms of it `asyncio` and 5 ms `logging`, both imported through
  `nuke_di.clients`; `nuke_di.core` itself is 3 ms. The figure was 27–37 ms in 1.9.0, a change inside its
  spread, whose p95 runs to 38–50 ms: a fresh interpreter is the noisiest figure of the suite.
  `nuke_di.fastapi` takes 153–187 ms against `fastapi`'s 151–191 ms, so what it adds is below the noise
  here. This is the figure the lazy-import work in [#14](https://github.com/troyan-dy/nuke-di/issues/14) moves.
- **A resolved client takes about 580 bytes**: 575–590 kB for the 1000-client mixed tree, as in 1.9.0.
- Between versions, `resolve()` with real type hints is within 25%, and with strings 3.13 and 3.14 are the
  slowest by far; `connect()` and the application are the same on every version, and the request figures of
  3.12 and 3.14 are a few percent above the others.

## Comparison with other libraries

`benchmarks/compare.py` runs the same trees through [dishka](https://github.com/reagento/dishka),
[wireup](https://github.com/maldoinc/wireup),
[dependency-injector](https://github.com/ets-labs/python-dependency-injector) and
[injector](https://github.com/python-injector/injector), the libraries a project choosing `nuke-di`
would otherwise consider. `make bench-compare` writes the JSON into `docs/benchmarks/`. Every library
gets the same classes, with the dependencies in the type hints of `__init__`, and does the same work:

| Scenario | The figure |
|----------|------------|
| `cold: container, registration, root` | Create a container, register the `N` classes and get the root, which constructs every client of the tree: what an application pays once at startup. The classes are made for every sample, outside the timing, as in the cold row of `benchmarks/run.py`, so nothing a library keeps per class serves the samples after the first: the same classes every sample would measure `nuke-di`'s second container, not a startup. For dishka and wireup it includes the validation of the graph their container does on creation; for dependency-injector, creating one `Singleton` provider per class on a `DynamicContainer`; for injector, an `Injector` with a binding per class. The `strings` trees go through the same code: every library reads the string annotations of a class in a registered module, dishka, wireup and injector through `get_type_hints()` as `nuke-di` does, dependency-injector not at all, its providers are wired by the names of the `__init__` arguments, so strings cost it nothing |
| `warm: the root again` | Get the root again from that container: the singleton, independent of `N` |
| `one request, a client in the handler` | One FastAPI request to a handler that takes one client through the library's integration: `nuke_di.fastapi`, `DishkaRoute` with `FromDishka[...]`, `wireup.integration.fastapi` with `Injected[...]`, `@inject` with `Depends(Provide[...])` for dependency-injector, each as its documentation shows. injector has no integration of its own |

What is done outside the timing, as a user does it at import: wireup's `@injectable` and
injector's `@inject` on the classes, once per tree, or once per sample in the cold scenario, whose classes are
new every sample; the latter evaluates the type hints of `__init__` right there,
so injector pays for string annotations at import and not in the figure. What stays inside: everything
a container creation involves.
The warm figure of `nuke-di` is `resolve()` of the root on a resolved container; `inject()` and the
framework integrations use the same cache.

![nuke-di against other DI libraries: lower is better](benchmarks/compare.png)

The chart is `benchmarks/chart.py` over the JSON of a run, and `compare.py --summary` prints the same
three figures as a table, plus the cold start of the string-annotation tree, the best per row in bold
with the ratio of the others to it; here at `N = 100`, from the full run below:

| Lower is better                                          | nuke-di        | dishka          | wireup          | dependency-injector | injector        |
|----------------------------------------------------------|---------------:|----------------:|----------------:|--------------------:|----------------:|
| Cold start: a container and a tree of 100 clients        | **527 µs**     | 12.4 ms (23.5×) | 21.7 ms (41.1×) | 1.23 ms (2.3×)      | 1.41 ms (2.7×)  |
| Cold start: the same 100 clients with string annotations | 1.17 ms (1.1×) | 13.3 ms (12.7×) | 25.3 ms (24.0×) | **1.05 ms**         | 1.61 ms (1.5×)  |
| A cached root                                            | 163 ns (4.3×)  | 267 ns (7.0×)   | 108 ns (2.8×)   | **38.3 ns**         | 1.27 µs (33.2×) |
| A FastAPI request with a client                          | **106 µs**     | 106 µs (1.0×)   | 237 µs (2.2×)   | 238 µs (2.3×)       | —               |

The comparison was taken on the same machine, Python 3.11.7, with `N = 10, 100, 1000` and 20 repeats,
on `nuke-di` 1.11.1, dishka 1.10.1, wireup 2.12.1, dependency-injector 4.49.1 and injector 0.24.0.

- **A cold tree costs 4–6.5 µs per client in `nuke-di`**, 2–3 times less than dependency-injector
  (9–13 µs) and injector (12–17 µs), which, like `nuke-di`, read the signatures and build the tree on demand:
  527 µs for 100 clients against 1.23 and 1.41 ms. dishka (90–310 µs per client) and wireup (200–590 µs)
  validate the whole graph when the container is created: 20–135 times more, 0.1–0.6 s for 1000 clients.
  That is the price of their startup checks, paid once. The 1.9.0 comparison had `nuke-di` at 7–13 µs and
  dependency-injector level with it; it also gave every sample the same classes, which since the per-class
  cache of 1.9.1 measures `nuke-di`'s second container: 158 µs for the 100 clients of the row above, 5.9 times
  ahead of dependency-injector, a startup no application has. The classes made per sample cost
  dependency-injector about 25% more too, injector 5–15% and dishka and wireup a few percent, most likely
  for the first attribute lookups on a new class.
- **With string annotations `nuke-di` costs 7–13 µs per client, 1.4–2.2 times its real-type figure**,
  which levels the cold-start row at 100 clients: dependency-injector (9–13 µs, wired by name, no
  annotation read) is 1.1 times ahead, injector (11–18 µs, the hints evaluated at import) 1.4 times behind.
  dishka and wireup read the strings too and pay at most 17% more for them, a part of their validation.
  The second container of `nuke-di` does not read them again, see the [Findings](#findings).
- **A cached root costs 37 ns–1.3 µs**: 37–40 ns in dependency-injector (Cython), 92–112 ns in wireup,
  159–180 ns in `nuke-di`, 256–310 ns in dishka, and 1.2–1.6 µs in injector, which resolves the binding on
  every `get()`. `nuke-di` was 108–119 ns in 1.9.0, close to wireup: the client check in front of the
  cache lookup since 1.11.0 is the difference, see the [Findings](#findings).
- **A FastAPI request through `nuke-di` or dishka costs 106 µs**, the same as a plain `Depends()`
  (102–114 µs in the baseline above). Through wireup it costs 237 µs and through dependency-injector
  238 µs, twice that: their integrations do more per request, as their documentation wires them; what
  exactly is not investigated here.
- A chain of 1000 clients exceeds the default recursion limit in dishka, wireup and injector, and in
  dependency-injector on 3.11 (not on 3.14); `nuke-di` resolves it without recursion since
  [#35](https://github.com/troyan-dy/nuke-di/issues/35). The runner raises the limit, which is enough for every
  library but injector.

### Python 3.11.7, nuke-di 1.11.1 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

nuke-di 1.11.1 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit d8dc493 · N = 10, 100, 1000 · 20 repeats

| Library             | Scenario                             | Shape          |    N |         Median |     p95 | Per client |
|---------------------|--------------------------------------|----------------|-----:|---------------:|--------:|-----------:|
| nuke-di             | cold: container, registration, root  | wide           |   10 |        56.7 µs | 70.4 µs |    5.67 µs |
| dishka              | cold: container, registration, root  | wide           |   10 |        1.43 ms | 1.81 ms |     143 µs |
| wireup              | cold: container, registration, root  | wide           |   10 |        2.29 ms | 4.80 ms |     229 µs |
| dependency-injector | cold: container, registration, root  | wide           |   10 |        94.7 µs |  116 µs |    9.47 µs |
| injector            | cold: container, registration, root  | wide           |   10 |         127 µs |  159 µs |    12.7 µs |
| nuke-di             | cold: container, registration, root  | wide           |  100 |         414 µs |  611 µs |    4.14 µs |
| dishka              | cold: container, registration, root  | wide           |  100 |        9.68 ms | 10.8 ms |    96.8 µs |
| wireup              | cold: container, registration, root  | wide           |  100 |        20.0 ms | 21.2 ms |     200 µs |
| dependency-injector | cold: container, registration, root  | wide           |  100 |         928 µs | 1.27 ms |    9.28 µs |
| injector            | cold: container, registration, root  | wide           |  100 |        1.27 ms | 1.48 ms |    12.7 µs |
| nuke-di             | cold: container, registration, root  | wide           | 1000 |        4.34 ms | 5.35 ms |    4.34 µs |
| dishka              | cold: container, registration, root  | wide           | 1000 |        91.1 ms |  105 ms |    91.1 µs |
| wireup              | cold: container, registration, root  | wide           | 1000 |         197 ms |  202 ms |     197 µs |
| dependency-injector | cold: container, registration, root  | wide           | 1000 |        13.2 ms | 14.2 ms |    13.2 µs |
| injector            | cold: container, registration, root  | wide           | 1000 |        12.0 ms | 13.0 ms |    12.0 µs |
| nuke-di             | cold: container, registration, root  | deep           |   10 |        55.9 µs | 79.2 µs |    5.59 µs |
| dishka              | cold: container, registration, root  | deep           |   10 |        3.07 ms | 9.19 ms |     307 µs |
| wireup              | cold: container, registration, root  | deep           |   10 |        4.52 ms | 6.75 ms |     452 µs |
| dependency-injector | cold: container, registration, root  | deep           |   10 |         105 µs |  139 µs |    10.5 µs |
| injector            | cold: container, registration, root  | deep           |   10 |         153 µs |  166 µs |    15.3 µs |
| nuke-di             | cold: container, registration, root  | deep           |  100 |         473 µs |  672 µs |    4.73 µs |
| dishka              | cold: container, registration, root  | deep           |  100 |        12.0 ms | 14.6 ms |     120 µs |
| wireup              | cold: container, registration, root  | deep           |  100 |        22.2 ms | 27.4 ms |     222 µs |
| dependency-injector | cold: container, registration, root  | deep           |  100 |        1.19 ms | 1.72 ms |    11.9 µs |
| injector            | cold: container, registration, root  | deep           |  100 |        1.46 ms | 2.10 ms |    14.6 µs |
| nuke-di             | cold: container, registration, root  | deep           | 1000 |        4.34 ms | 5.43 ms |    4.34 µs |
| dishka              | cold: container, registration, root  | deep           | 1000 |         155 ms |  225 ms |     155 µs |
| wireup              | cold: container, registration, root  | deep           | 1000 |         585 ms |  656 ms |     585 µs |
| dependency-injector | cold: container, registration, root  | deep           | 1000 |        10.2 ms | 11.8 ms |    10.2 µs |
| injector            | cold: container, registration, root  | deep           | 1000 | RecursionError |         |            |
| nuke-di             | cold: container, registration, root  | mixed          |   10 |        65.2 µs | 86.9 µs |    6.52 µs |
| dishka              | cold: container, registration, root  | mixed          |   10 |        1.76 ms | 2.06 ms |     176 µs |
| wireup              | cold: container, registration, root  | mixed          |   10 |        2.40 ms | 2.69 ms |     240 µs |
| dependency-injector | cold: container, registration, root  | mixed          |   10 |         116 µs |  157 µs |    11.6 µs |
| injector            | cold: container, registration, root  | mixed          |   10 |         163 µs |  217 µs |    16.3 µs |
| nuke-di             | cold: container, registration, root  | mixed          |  100 |         527 µs |  725 µs |    5.27 µs |
| dishka              | cold: container, registration, root  | mixed          |  100 |        12.4 ms | 13.1 ms |     124 µs |
| wireup              | cold: container, registration, root  | mixed          |  100 |        21.7 ms | 22.8 ms |     217 µs |
| dependency-injector | cold: container, registration, root  | mixed          |  100 |        1.23 ms | 1.36 ms |    12.3 µs |
| injector            | cold: container, registration, root  | mixed          |  100 |        1.41 ms | 1.68 ms |    14.1 µs |
| nuke-di             | cold: container, registration, root  | mixed          | 1000 |        5.13 ms | 5.56 ms |    5.13 µs |
| dishka              | cold: container, registration, root  | mixed          | 1000 |         114 ms |  127 ms |     114 µs |
| wireup              | cold: container, registration, root  | mixed          | 1000 |         238 ms |  253 ms |     238 µs |
| dependency-injector | cold: container, registration, root  | mixed          | 1000 |        10.8 ms | 11.6 ms |    10.8 µs |
| injector            | cold: container, registration, root  | mixed          | 1000 |        17.4 ms | 18.3 ms |    17.4 µs |
| nuke-di             | cold: container, registration, root  | wide, strings  |   10 |        80.3 µs |  119 µs |    8.03 µs |
| dishka              | cold: container, registration, root  | wide, strings  |   10 |        1.49 ms | 1.61 ms |     149 µs |
| wireup              | cold: container, registration, root  | wide, strings  |   10 |        2.48 ms | 2.76 ms |     248 µs |
| dependency-injector | cold: container, registration, root  | wide, strings  |   10 |         106 µs |  148 µs |    10.6 µs |
| injector            | cold: container, registration, root  | wide, strings  |   10 |         138 µs |  212 µs |    13.8 µs |
| nuke-di             | cold: container, registration, root  | wide, strings  |  100 |         722 µs |  901 µs |    7.22 µs |
| dishka              | cold: container, registration, root  | wide, strings  |  100 |        9.67 ms | 10.4 ms |    96.7 µs |
| wireup              | cold: container, registration, root  | wide, strings  |  100 |        19.2 ms | 20.0 ms |     192 µs |
| dependency-injector | cold: container, registration, root  | wide, strings  |  100 |         911 µs | 1.09 ms |    9.11 µs |
| injector            | cold: container, registration, root  | wide, strings  |  100 |        1.09 ms | 1.33 ms |    10.9 µs |
| nuke-di             | cold: container, registration, root  | wide, strings  | 1000 |        7.83 ms | 8.34 ms |    7.83 µs |
| dishka              | cold: container, registration, root  | wide, strings  | 1000 |        93.3 ms |  100 ms |    93.3 µs |
| wireup              | cold: container, registration, root  | wide, strings  | 1000 |         215 ms |  226 ms |     215 µs |
| dependency-injector | cold: container, registration, root  | wide, strings  | 1000 |        12.9 ms | 14.4 ms |    12.9 µs |
| injector            | cold: container, registration, root  | wide, strings  | 1000 |        11.7 ms | 12.8 ms |    11.7 µs |
| nuke-di             | cold: container, registration, root  | deep, strings  |   10 |        77.8 µs | 81.5 µs |    7.78 µs |
| dishka              | cold: container, registration, root  | deep, strings  |   10 |        1.86 ms | 2.34 ms |     186 µs |
| wireup              | cold: container, registration, root  | deep, strings  |   10 |        2.37 ms | 3.12 ms |     237 µs |
| dependency-injector | cold: container, registration, root  | deep, strings  |   10 |         102 µs |  120 µs |    10.2 µs |
| injector            | cold: container, registration, root  | deep, strings  |   10 |         160 µs |  251 µs |    16.0 µs |
| nuke-di             | cold: container, registration, root  | deep, strings  |  100 |         724 µs |  897 µs |    7.24 µs |
| dishka              | cold: container, registration, root  | deep, strings  |  100 |        12.9 ms | 13.7 ms |     129 µs |
| wireup              | cold: container, registration, root  | deep, strings  |  100 |        22.6 ms | 24.3 ms |     226 µs |
| dependency-injector | cold: container, registration, root  | deep, strings  |  100 |         937 µs | 1.11 ms |    9.37 µs |
| injector            | cold: container, registration, root  | deep, strings  |  100 |        1.44 ms | 2.04 ms |    14.4 µs |
| nuke-di             | cold: container, registration, root  | deep, strings  | 1000 |        7.89 ms | 8.66 ms |    7.89 µs |
| dishka              | cold: container, registration, root  | deep, strings  | 1000 |         144 ms |  149 ms |     144 µs |
| wireup              | cold: container, registration, root  | deep, strings  | 1000 |         619 ms |  663 ms |     619 µs |
| dependency-injector | cold: container, registration, root  | deep, strings  | 1000 |        10.2 ms | 11.2 ms |    10.2 µs |
| injector            | cold: container, registration, root  | deep, strings  | 1000 | RecursionError |         |            |
| nuke-di             | cold: container, registration, root  | mixed, strings |   10 |         128 µs |  164 µs |    12.8 µs |
| dishka              | cold: container, registration, root  | mixed, strings |   10 |        1.81 ms | 2.33 ms |     181 µs |
| wireup              | cold: container, registration, root  | mixed, strings |   10 |        2.55 ms | 4.34 ms |     255 µs |
| dependency-injector | cold: container, registration, root  | mixed, strings |   10 |         119 µs |  197 µs |    11.9 µs |
| injector            | cold: container, registration, root  | mixed, strings |   10 |         158 µs |  187 µs |    15.8 µs |
| nuke-di             | cold: container, registration, root  | mixed, strings |  100 |        1.17 ms | 3.03 ms |    11.7 µs |
| dishka              | cold: container, registration, root  | mixed, strings |  100 |        13.3 ms | 15.7 ms |     133 µs |
| wireup              | cold: container, registration, root  | mixed, strings |  100 |        25.3 ms | 31.2 ms |     253 µs |
| dependency-injector | cold: container, registration, root  | mixed, strings |  100 |        1.05 ms | 1.48 ms |    10.5 µs |
| injector            | cold: container, registration, root  | mixed, strings |  100 |        1.61 ms | 3.03 ms |    16.1 µs |
| nuke-di             | cold: container, registration, root  | mixed, strings | 1000 |        10.9 ms | 12.4 ms |    10.9 µs |
| dishka              | cold: container, registration, root  | mixed, strings | 1000 |         121 ms |  131 ms |     121 µs |
| wireup              | cold: container, registration, root  | mixed, strings | 1000 |         276 ms |  289 ms |     276 µs |
| dependency-injector | cold: container, registration, root  | mixed, strings | 1000 |        10.4 ms | 11.9 ms |    10.4 µs |
| injector            | cold: container, registration, root  | mixed, strings | 1000 |        17.8 ms | 22.0 ms |    17.8 µs |
| nuke-di             | warm: the root again                 | wide           |   10 |         165 ns |  172 ns |            |
| dishka              | warm: the root again                 | wide           |   10 |         256 ns |  266 ns |            |
| wireup              | warm: the root again                 | wide           |   10 |        92.4 ns | 94.0 ns |            |
| dependency-injector | warm: the root again                 | wide           |   10 |        39.7 ns | 45.4 ns |            |
| injector            | warm: the root again                 | wide           |   10 |        1.23 µs | 1.30 µs |            |
| nuke-di             | warm: the root again                 | wide           |  100 |         159 ns |  166 ns |            |
| dishka              | warm: the root again                 | wide           |  100 |         268 ns |  297 ns |            |
| wireup              | warm: the root again                 | wide           |  100 |         104 ns |  109 ns |            |
| dependency-injector | warm: the root again                 | wide           |  100 |        38.7 ns | 39.5 ns |            |
| injector            | warm: the root again                 | wide           |  100 |        1.22 µs | 1.35 µs |            |
| nuke-di             | warm: the root again                 | wide           | 1000 |         166 ns |  176 ns |            |
| dishka              | warm: the root again                 | wide           | 1000 |         270 ns |  283 ns |            |
| wireup              | warm: the root again                 | wide           | 1000 |        94.3 ns | 99.9 ns |            |
| dependency-injector | warm: the root again                 | wide           | 1000 |        39.0 ns | 56.0 ns |            |
| injector            | warm: the root again                 | wide           | 1000 |        1.25 µs | 1.34 µs |            |
| nuke-di             | warm: the root again                 | deep           |   10 |         166 ns |  182 ns |            |
| dishka              | warm: the root again                 | deep           |   10 |         283 ns |  451 ns |            |
| wireup              | warm: the root again                 | deep           |   10 |        97.3 ns |  117 ns |            |
| dependency-injector | warm: the root again                 | deep           |   10 |        39.1 ns | 77.1 ns |            |
| injector            | warm: the root again                 | deep           |   10 |        1.40 µs | 2.33 µs |            |
| nuke-di             | warm: the root again                 | deep           |  100 |         164 ns |  168 ns |            |
| dishka              | warm: the root again                 | deep           |  100 |         264 ns |  306 ns |            |
| wireup              | warm: the root again                 | deep           |  100 |         101 ns |  106 ns |            |
| dependency-injector | warm: the root again                 | deep           |  100 |        39.7 ns | 51.1 ns |            |
| injector            | warm: the root again                 | deep           |  100 |        1.24 µs | 1.40 µs |            |
| nuke-di             | warm: the root again                 | deep           | 1000 |         179 ns |  538 ns |            |
| dishka              | warm: the root again                 | deep           | 1000 |         295 ns |  337 ns |            |
| wireup              | warm: the root again                 | deep           | 1000 |        92.3 ns | 94.1 ns |            |
| dependency-injector | warm: the root again                 | deep           | 1000 |        37.6 ns | 38.0 ns |            |
| injector            | warm: the root again                 | deep           | 1000 | RecursionError |         |            |
| nuke-di             | warm: the root again                 | mixed          |   10 |         164 ns |  176 ns |            |
| dishka              | warm: the root again                 | mixed          |   10 |         310 ns |  628 ns |            |
| wireup              | warm: the root again                 | mixed          |   10 |         112 ns |  194 ns |            |
| dependency-injector | warm: the root again                 | mixed          |   10 |        40.2 ns |  176 ns |            |
| injector            | warm: the root again                 | mixed          |   10 |        1.64 µs | 2.09 µs |            |
| nuke-di             | warm: the root again                 | mixed          |  100 |         163 ns |  194 ns |            |
| dishka              | warm: the root again                 | mixed          |  100 |         267 ns |  304 ns |            |
| wireup              | warm: the root again                 | mixed          |  100 |         108 ns |  193 ns |            |
| dependency-injector | warm: the root again                 | mixed          |  100 |        38.3 ns | 41.8 ns |            |
| injector            | warm: the root again                 | mixed          |  100 |        1.27 µs | 1.41 µs |            |
| nuke-di             | warm: the root again                 | mixed          | 1000 |         163 ns |  188 ns |            |
| dishka              | warm: the root again                 | mixed          | 1000 |         273 ns |  338 ns |            |
| wireup              | warm: the root again                 | mixed          | 1000 |        95.6 ns |  111 ns |            |
| dependency-injector | warm: the root again                 | mixed          | 1000 |        37.1 ns | 37.3 ns |            |
| injector            | warm: the root again                 | mixed          | 1000 |        1.20 µs | 1.29 µs |            |
| nuke-di             | one request, a client in the handler | FastAPI        |      |         106 µs |  144 µs |            |
| dishka              | one request, a client in the handler | FastAPI        |      |         106 µs |  110 µs |            |
| wireup              | one request, a client in the handler | FastAPI        |      |         237 µs |  301 µs |            |
| dependency-injector | one request, a client in the handler | FastAPI        |      |         238 µs |  290 µs |            |

## Baseline

### Python 3.11.7

nuke-di 1.11.1 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit d8dc493 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |     p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 49.5 µs | 59.6 µs |    4.95 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 12.9 µs | 13.8 µs |    1.29 µs |
| resolve(), warm                                                       | wide                                         |   10 |  164 ns |  167 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  484 µs | 1.37 ms |    4.84 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  136 µs |  193 µs |    1.36 µs |
| resolve(), warm                                                       | wide                                         |  100 |  158 ns |  203 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 4.79 ms | 6.13 ms |    4.79 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 2.08 ms | 2.37 ms |    2.08 µs |
| resolve(), warm                                                       | wide                                         | 1000 |  160 ns |  186 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 45.6 µs | 62.6 µs |    4.56 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 13.4 µs | 18.0 µs |    1.34 µs |
| resolve(), warm                                                       | deep                                         |   10 |  170 ns |  473 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  418 µs |  578 µs |    4.18 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  133 µs |  153 µs |    1.33 µs |
| resolve(), warm                                                       | deep                                         |  100 |  157 ns |  171 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 4.64 ms | 6.31 ms |    4.64 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 1.35 ms | 1.65 ms |    1.35 µs |
| resolve(), warm                                                       | deep                                         | 1000 |  153 ns |  164 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 | 58.1 µs | 61.7 µs |    5.81 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 14.9 µs | 16.4 µs |    1.49 µs |
| resolve(), warm                                                       | mixed                                        |   10 |  155 ns |  161 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 |  497 µs |  627 µs |    4.97 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  149 µs |  163 µs |    1.49 µs |
| resolve(), warm                                                       | mixed                                        |  100 |  156 ns |  167 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 4.89 ms | 5.91 ms |    4.89 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 1.51 ms | 1.78 ms |    1.51 µs |
| resolve(), warm                                                       | mixed                                        | 1000 |  158 ns |  163 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 | 75.3 µs | 81.3 µs |    7.53 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 | 13.3 µs | 14.5 µs |    1.33 µs |
| resolve(), cold                                                       | wide, strings                                |  100 |  730 µs |  779 µs |    7.30 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 |  136 µs |  175 µs |    1.36 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 7.77 ms | 8.51 ms |    7.77 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 1.75 ms | 2.09 ms |    1.75 µs |
| resolve(), cold                                                       | deep, strings                                |   10 | 76.4 µs | 85.1 µs |    7.64 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 | 13.4 µs | 14.3 µs |    1.34 µs |
| resolve(), cold                                                       | deep, strings                                |  100 |  721 µs |  988 µs |    7.21 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 |  131 µs |  156 µs |    1.31 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 8.09 ms | 9.08 ms |    8.09 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 1.49 ms | 1.95 ms |    1.49 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  119 µs |  132 µs |    11.9 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 | 14.9 µs | 16.6 µs |    1.49 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 1.08 ms | 1.34 ms |    10.8 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 |  152 µs |  172 µs |    1.52 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 9.98 ms | 10.8 ms |    9.98 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 1.50 ms | 1.87 ms |    1.50 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  236 µs |  247 µs |    23.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 2.58 µs | 3.10 µs |     258 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  234 µs |  244 µs |    23.4 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 1.36 ms | 2.21 ms |    13.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 19.9 µs | 45.7 µs |     199 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 1.34 ms | 2.16 ms |    13.4 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 11.1 ms | 12.2 ms |    11.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  189 µs |  239 µs |     189 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 10.9 ms | 12.0 ms |    10.9 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 |  988 µs | 1.05 ms |    98.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.60 µs | 3.39 µs |     260 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 |  986 µs | 1.05 ms |    98.6 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 10.4 ms | 12.0 ms |     104 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 23.0 µs | 39.5 µs |     230 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 10.4 ms | 12.0 ms |     104 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 |  105 ms |  145 ms |     105 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  412 µs |  662 µs |     412 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 |  105 ms |  145 ms |     105 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  111 ms |  111 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 87.1 ms | 87.4 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 23.7 ms | 24.3 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 7.97 µs | 8.23 µs |            |
| call of the injected function                                         | 2 clients                                    |      | 84.0 ns | 86.2 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 36.9 ns | 37.8 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 | 64.7 µs | 87.9 µs |    6.47 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 |  572 µs |  909 µs |    5.72 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 5.69 ms | 6.07 ms |    5.69 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 | 77.7 µs | 87.2 µs |    7.77 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 |  678 µs |  797 µs |    6.78 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 7.00 ms | 11.6 ms |    7.00 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 |  713 µs |  864 µs |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 18.4 µs | 21.8 µs |    1.84 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 |  863 µs |  966 µs |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  159 µs |  166 µs |    1.59 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 3.12 ms | 3.77 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 2.09 ms | 2.47 ms |    2.09 µs |
| one request                                                           | a client through nuke-di                     |      |  105 µs |  108 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  102 µs |  168 µs |            |
| one request                                                           | no dependencies                              |      | 97.6 µs |  102 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 29.2 ms | 38.2 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  153 ms |  197 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  151 ms |  244 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  583 kB |  659 kB |      583 B |

### Python 3.12.5

nuke-di 1.11.1 · CPython 3.12.5 · macOS-26.6.2-arm64-arm-64bit · commit d8dc493 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |     p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 48.8 µs | 56.5 µs |    4.88 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 12.7 µs | 13.9 µs |    1.27 µs |
| resolve(), warm                                                       | wide                                         |   10 |  173 ns |  178 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  398 µs |  468 µs |    3.98 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  132 µs |  137 µs |    1.32 µs |
| resolve(), warm                                                       | wide                                         |  100 |  172 ns |  173 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 4.65 ms | 6.35 ms |    4.65 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 1.97 ms | 3.78 ms |    1.97 µs |
| resolve(), warm                                                       | wide                                         | 1000 |  180 ns |  306 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 52.8 µs | 65.0 µs |    5.28 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 13.4 µs | 16.1 µs |    1.34 µs |
| resolve(), warm                                                       | deep                                         |   10 |  178 ns |  202 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  454 µs |  550 µs |    4.54 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  148 µs |  162 µs |    1.48 µs |
| resolve(), warm                                                       | deep                                         |  100 |  183 ns |  199 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 4.71 ms | 5.57 ms |    4.71 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 1.45 ms | 1.85 ms |    1.45 µs |
| resolve(), warm                                                       | deep                                         | 1000 |  183 ns |  212 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 | 63.5 µs | 75.4 µs |    6.35 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 15.4 µs | 22.1 µs |    1.54 µs |
| resolve(), warm                                                       | mixed                                        |   10 |  179 ns |  189 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 |  515 µs |  611 µs |    5.15 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  159 µs |  183 µs |    1.59 µs |
| resolve(), warm                                                       | mixed                                        |  100 |  182 ns |  373 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 5.08 ms | 5.94 ms |    5.08 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 1.87 ms | 2.13 ms |    1.87 µs |
| resolve(), warm                                                       | mixed                                        | 1000 |  185 ns |  247 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 |  110 µs |  165 µs |    11.0 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 | 12.7 µs | 13.7 µs |    1.27 µs |
| resolve(), cold                                                       | wide, strings                                |  100 | 1.01 ms | 1.14 ms |    10.1 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 |  131 µs |  138 µs |    1.31 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 9.84 ms | 11.2 ms |    9.84 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 1.60 ms | 1.86 ms |    1.60 µs |
| resolve(), cold                                                       | deep, strings                                |   10 | 80.7 µs | 89.1 µs |    8.07 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 | 13.5 µs | 15.9 µs |    1.35 µs |
| resolve(), cold                                                       | deep, strings                                |  100 |  815 µs |  921 µs |    8.15 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 |  136 µs |  144 µs |    1.36 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 10.9 ms | 11.5 ms |    10.9 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 1.41 ms | 1.51 ms |    1.41 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  168 µs |  177 µs |    16.8 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 | 15.8 µs | 16.9 µs |    1.58 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 1.67 ms | 1.91 ms |    16.7 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 |  157 µs |  170 µs |    1.57 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 14.5 ms | 15.7 ms |    14.5 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 2.05 ms | 3.53 ms |    2.05 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  256 µs |  364 µs |    25.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 2.60 µs | 3.76 µs |     260 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  253 µs |  361 µs |    25.3 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 1.41 ms | 1.63 ms |    14.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 17.8 µs | 22.5 µs |     178 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 1.40 ms | 1.60 ms |    14.0 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 11.8 ms | 12.5 ms |    11.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  165 µs |  210 µs |     165 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 11.7 ms | 12.3 ms |    11.7 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 | 1.00 ms | 1.10 ms |     100 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.25 µs | 2.92 µs |     225 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 | 1.00 ms | 1.09 ms |     100 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 9.75 ms | 9.92 ms |    97.5 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 17.8 µs | 24.9 µs |     178 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 9.72 ms | 9.91 ms |    97.2 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 | 97.9 ms |  106 ms |    97.9 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  286 µs |  368 µs |     286 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 | 97.6 ms |  106 ms |    97.6 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  111 ms |  111 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 87.0 ms | 87.2 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 23.6 ms | 24.1 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 8.97 µs | 9.20 µs |            |
| call of the injected function                                         | 2 clients                                    |      |  126 ns |  131 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 40.6 ns | 41.8 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 | 64.4 µs | 74.5 µs |    6.44 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 |  531 µs |  576 µs |    5.31 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 5.61 ms | 6.42 ms |    5.61 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 | 78.6 µs | 96.9 µs |    7.86 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 |  669 µs |  892 µs |    6.69 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 7.15 ms | 13.2 ms |    7.15 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 |  732 µs |  848 µs |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 19.2 µs | 24.3 µs |    1.92 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 |  887 µs | 1.05 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  174 µs |  190 µs |    1.74 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 2.54 ms | 3.11 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 1.66 ms | 2.43 ms |    1.66 µs |
| one request                                                           | a client through nuke-di                     |      |  114 µs |  161 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  114 µs |  259 µs |            |
| one request                                                           | no dependencies                              |      | 97.6 µs |  147 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 40.7 ms | 50.4 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  179 ms |  215 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  191 ms |  235 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  574 kB |  651 kB |      574 B |

### Python 3.13.14

nuke-di 1.11.1 · CPython 3.13.14 · macOS-26.6.2-arm64-arm-64bit-Mach-O · commit d8dc493 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |     p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 48.0 µs | 62.0 µs |    4.80 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 11.8 µs | 12.4 µs |    1.18 µs |
| resolve(), warm                                                       | wide                                         |   10 |  162 ns |  165 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  410 µs |  478 µs |    4.10 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  121 µs |  127 µs |    1.21 µs |
| resolve(), warm                                                       | wide                                         |  100 |  161 ns |  168 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 4.78 ms | 6.05 ms |    4.78 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 1.63 ms | 2.35 ms |    1.63 µs |
| resolve(), warm                                                       | wide                                         | 1000 |  168 ns |  169 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 50.2 µs | 75.8 µs |    5.02 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 12.3 µs | 15.3 µs |    1.23 µs |
| resolve(), warm                                                       | deep                                         |   10 |  165 ns |  173 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  438 µs |  568 µs |    4.38 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  127 µs |  133 µs |    1.27 µs |
| resolve(), warm                                                       | deep                                         |  100 |  164 ns |  172 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 4.69 ms | 5.16 ms |    4.69 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 1.32 ms | 1.78 ms |    1.32 µs |
| resolve(), warm                                                       | deep                                         | 1000 |  159 ns |  163 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 | 59.5 µs | 73.4 µs |    5.95 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 14.2 µs | 17.2 µs |    1.42 µs |
| resolve(), warm                                                       | mixed                                        |   10 |  167 ns |  179 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 |  559 µs |  609 µs |    5.59 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  143 µs |  155 µs |    1.43 µs |
| resolve(), warm                                                       | mixed                                        |  100 |  162 ns |  166 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 5.33 ms | 5.86 ms |    5.33 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 1.60 ms | 1.98 ms |    1.60 µs |
| resolve(), warm                                                       | mixed                                        | 1000 |  167 ns |  174 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 |  122 µs |  139 µs |    12.2 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 | 11.8 µs | 12.5 µs |    1.18 µs |
| resolve(), cold                                                       | wide, strings                                |  100 | 1.26 ms | 1.45 ms |    12.6 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 |  134 µs |  280 µs |    1.34 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 13.2 ms | 20.6 ms |    13.2 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 1.54 ms | 1.73 ms |    1.54 µs |
| resolve(), cold                                                       | deep, strings                                |   10 |  126 µs |  145 µs |    12.6 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 | 12.6 µs | 15.1 µs |    1.26 µs |
| resolve(), cold                                                       | deep, strings                                |  100 | 1.34 ms | 1.98 ms |    13.4 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 |  126 µs |  151 µs |    1.26 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 14.6 ms | 15.3 ms |    14.6 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 1.43 ms | 1.92 ms |    1.43 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  242 µs |  285 µs |    24.2 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 | 14.2 µs | 15.9 µs |    1.42 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 2.28 ms | 2.59 ms |    22.8 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 |  139 µs |  153 µs |    1.39 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 19.5 ms | 22.8 ms |    19.5 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 1.69 ms | 2.05 ms |    1.69 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  292 µs |  428 µs |    29.2 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 2.40 µs | 3.26 µs |     240 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  289 µs |  425 µs |    28.9 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 1.30 ms | 1.41 ms |    13.0 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 15.8 µs | 21.3 µs |     158 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 1.28 ms | 1.39 ms |    12.8 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 10.9 ms | 11.9 ms |    10.9 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  156 µs |  224 µs |     156 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 10.7 ms | 11.7 ms |    10.7 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 | 1.02 ms | 1.12 ms |     102 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.46 µs | 2.89 µs |     246 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 | 1.02 ms | 1.12 ms |     102 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 10.0 ms | 12.2 ms |     100 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 17.7 µs | 25.9 µs |     177 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 9.98 ms | 12.2 ms |    99.8 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 |  101 ms |  113 ms |     101 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  212 µs |  329 µs |     212 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 |  101 ms |  113 ms |     101 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  111 ms |  112 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 87.0 ms | 89.5 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 23.5 ms | 24.4 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 9.84 µs | 10.1 µs |            |
| call of the injected function                                         | 2 clients                                    |      |  109 ns |  110 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 39.0 ns | 50.5 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 | 73.9 µs | 89.6 µs |    7.39 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 |  570 µs |  703 µs |    5.70 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 5.88 ms | 6.15 ms |    5.88 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 | 76.3 µs | 79.7 µs |    7.63 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 |  674 µs |  722 µs |    6.74 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 6.87 ms | 7.08 ms |    6.87 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 |  884 µs | 1.05 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 17.8 µs | 25.8 µs |    1.78 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 | 1.09 ms | 1.25 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  151 µs |  181 µs |    1.51 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 2.75 ms | 3.24 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 1.77 ms | 1.94 ms |    1.77 µs |
| one request                                                           | a client through nuke-di                     |      |  107 µs |  111 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  108 µs |  114 µs |            |
| one request                                                           | no dependencies                              |      |  101 µs |  105 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 36.4 ms | 41.4 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  179 ms |  228 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  160 ms |  180 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  590 kB |  667 kB |      590 B |

### Python 3.14.6

nuke-di 1.11.1 · CPython 3.14.6 · macOS-26.6.2-arm64-arm-64bit-Mach-O · commit d8dc493 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |     p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 50.8 µs | 56.5 µs |    5.08 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 11.9 µs | 15.3 µs |    1.19 µs |
| resolve(), warm                                                       | wide                                         |   10 |  166 ns |  169 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  434 µs |  486 µs |    4.34 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  123 µs |  153 µs |    1.23 µs |
| resolve(), warm                                                       | wide                                         |  100 |  153 ns |  154 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 4.64 ms | 5.70 ms |    4.64 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 1.41 ms | 1.70 ms |    1.41 µs |
| resolve(), warm                                                       | wide                                         | 1000 |  152 ns |  176 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 46.3 µs | 49.6 µs |    4.63 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 12.3 µs | 13.1 µs |    1.23 µs |
| resolve(), warm                                                       | deep                                         |   10 |  154 ns |  167 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  448 µs |  650 µs |    4.48 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  122 µs |  137 µs |    1.22 µs |
| resolve(), warm                                                       | deep                                         |  100 |  159 ns |  175 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 4.88 ms | 5.68 ms |    4.88 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 1.30 ms | 1.42 ms |    1.30 µs |
| resolve(), warm                                                       | deep                                         | 1000 |  153 ns |  157 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 | 63.4 µs | 77.9 µs |    6.34 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 14.0 µs | 15.2 µs |    1.40 µs |
| resolve(), warm                                                       | mixed                                        |   10 |  160 ns |  174 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 |  532 µs |  687 µs |    5.32 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  140 µs |  149 µs |    1.40 µs |
| resolve(), warm                                                       | mixed                                        |  100 |  155 ns |  164 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 5.58 ms | 9.22 ms |    5.58 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 2.18 ms | 8.18 ms |    2.18 µs |
| resolve(), warm                                                       | mixed                                        | 1000 |  161 ns |  178 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 |  126 µs |  168 µs |    12.6 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 | 11.8 µs | 12.9 µs |    1.18 µs |
| resolve(), cold                                                       | wide, strings                                |  100 | 1.49 ms | 3.66 ms |    14.9 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 |  123 µs |  205 µs |    1.23 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 20.4 ms | 27.0 ms |    20.4 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 1.56 ms | 1.86 ms |    1.56 µs |
| resolve(), cold                                                       | deep, strings                                |   10 |  116 µs |  151 µs |    11.6 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 | 12.0 µs | 14.6 µs |    1.20 µs |
| resolve(), cold                                                       | deep, strings                                |  100 | 1.45 ms | 3.96 ms |    14.5 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 |  133 µs |  184 µs |    1.33 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 20.6 ms | 33.2 ms |    20.6 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 1.64 ms | 2.12 ms |    1.64 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  257 µs |  549 µs |    25.7 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 | 14.9 µs | 34.3 µs |    1.49 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 1.94 ms | 2.78 ms |    19.4 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 |  142 µs |  210 µs |    1.42 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 29.2 ms | 36.5 ms |    29.2 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 1.70 ms | 2.10 ms |    1.70 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  215 µs |  303 µs |    21.5 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 2.29 µs | 2.95 µs |     229 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  213 µs |  300 µs |    21.3 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 1.15 ms | 1.30 ms |    11.5 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 16.5 µs | 26.7 µs |     165 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 1.13 ms | 1.28 ms |    11.3 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 10.3 ms | 11.0 ms |    10.3 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  176 µs |  277 µs |     176 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 10.0 ms | 10.8 ms |    10.0 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 |  915 µs |  953 µs |    91.5 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.35 µs | 2.63 µs |     235 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 |  913 µs |  951 µs |    91.3 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 11.7 ms | 18.1 ms |     117 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 27.9 µs | 75.8 µs |     279 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 11.7 ms | 18.1 ms |     117 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 |  103 ms |  151 ms |     103 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  258 µs |  299 µs |     258 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 |  102 ms |  151 ms |     102 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  111 ms |  116 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 87.0 ms | 87.4 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 23.6 ms | 28.1 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 9.59 µs | 10.4 µs |            |
| call of the injected function                                         | 2 clients                                    |      |  118 ns |  121 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 37.5 ns | 38.1 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 | 72.1 µs | 95.8 µs |    7.21 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 |  600 µs |  782 µs |    6.00 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 6.03 ms | 7.36 ms |    6.03 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 | 77.5 µs | 91.0 µs |    7.75 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 |  723 µs |  849 µs |    7.23 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 7.32 ms | 7.95 ms |    7.32 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 |  819 µs |  897 µs |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 18.2 µs | 26.2 µs |    1.82 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 | 1.00 ms | 1.22 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  154 µs |  170 µs |    1.54 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 2.70 ms | 2.93 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 1.52 ms | 1.82 ms |    1.52 µs |
| one request                                                           | a client through nuke-di                     |      |  117 µs |  134 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  113 µs |  125 µs |            |
| one request                                                           | no dependencies                              |      |  107 µs |  113 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 35.7 ms | 39.8 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  187 ms |  203 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  180 ms |  282 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  590 kB |  667 kB |      590 B |
