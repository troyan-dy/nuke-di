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

It prints a Markdown table with the median and the p95 of the repeats and a figure per client:

```console
$ uv run python benchmarks/run.py --only resolve --size 100
nuke-di 1.13.0 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 36ddfff · N = 100 · 20 repeats

| Scenario                                         | Shape          |   N |  Median |     p95 | Per client |
|--------------------------------------------------|----------------|----:|--------:|--------:|-----------:|
| resolve(), cold                                  | wide           | 100 |  354 µs |  482 µs |    3.54 µs |
| resolve(), second container, classes seen before | wide           | 100 |  102 µs |  146 µs |    1.02 µs |
| resolve(), warm                                  | wide           | 100 | 89.8 ns | 96.2 ns |            |
| resolve(), cold                                  | deep           | 100 |  342 µs |  400 µs |    3.42 µs |
| resolve(), second container, classes seen before | deep           | 100 | 94.7 µs | 99.4 µs |     947 ns |
| resolve(), warm                                  | deep           | 100 | 88.8 ns | 94.6 ns |            |
| resolve(), cold                                  | mixed          | 100 |  443 µs |  587 µs |    4.43 µs |
| resolve(), second container, classes seen before | mixed          | 100 |  109 µs |  172 µs |    1.09 µs |
| resolve(), warm                                  | mixed          | 100 | 87.8 ns | 92.1 ns |            |
| resolve(), cold                                  | wide, strings  | 100 |  655 µs |  791 µs |    6.55 µs |
| resolve(), second container, classes seen before | wide, strings  | 100 | 96.0 µs |  110 µs |     960 ns |
| resolve(), cold                                  | deep, strings  | 100 |  690 µs |  850 µs |    6.90 µs |
| resolve(), second container, classes seen before | deep, strings  | 100 |  118 µs |  196 µs |    1.18 µs |
| resolve(), cold                                  | mixed, strings | 100 | 1.05 ms | 3.30 ms |    10.5 µs |
| resolve(), second container, classes seen before | mixed, strings | 100 | 97.4 µs |  106 µs |     974 ns |
```

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
| `resolve(), cold` | `wide`: one root that declares `N - 1` clients without dependencies | `resolve()` of the root on a fresh `Dependencies()`, the classes never resolved before: a fresh tree per sample, so nothing a process keeps per class serves them |
| | `deep`: a chain of `N` clients | |
| | `mixed`: a pyramid 1, 2, 4, ... wide from the top, every client depends on two or three of the row below, about log2(N) clients deep | |
| | `wide, strings`, `deep, strings`, `mixed, strings`: the same trees with string annotations | |
| `resolve(), second container, classes seen before` | the same six trees | `resolve()` of the root on a fresh `Dependencies()` after the classes were resolved once in the process: what every test of a session pays after the first, and the row the per-class cache of 1.9.1 ([#29](https://github.com/troyan-dy/nuke-di/issues/29)) moved |
| `resolve(), warm` | the three trees with real type hints | A second `resolve()` of the same root: the singleton cache hit, independent of `N` and of the hints |
| `connect() + disconnect()` | `wide`: `N` independent clients, connected concurrently; `deep`: a chain of `N`, connected one after another | One `connect()` and `disconnect()` of the container |
| `..., ideal` | the same | The same clients' `connect()` and `disconnect()` coroutines awaited directly, without the container |
| `..., overhead above the ideal` | the same | The difference of the two, sample by sample: what the scheduling costs |
| `connect() + disconnect(), wall time` | `application`: 8 clients whose `connect()` and `disconnect()` sleep for the time a real connection takes, 1–60 ms, in the tree below, which has slack between its branches | The wall time of one `connect()` and `disconnect()` of the container: the sleeps, not the scheduling |
| `..., ideal: the critical path` | the same | The same coroutines scheduled by hand, every `connect()` started as soon as the client's dependencies are connected and every `disconnect()` as soon as its consumers are disconnected: the longest chain of the tree, the least any schedule can take |
| `..., above the critical path` | the same | The difference of the two, sample by sample: what the container's schedule adds to the sleeps |
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
`Api(Users, Consumer)` 2 / 1. The critical path is `Settings` → `Kafka` → `Consumer` → `Api`, 68 ms, and
`Api` → `Consumer` → `Kafka` back, 16 ms: 84 ms, which is what the container takes since 1.13.0, where every
client connects as soon as its own dependencies have ([#28](https://github.com/troyan-dy/nuke-di/issues/28)).
Up to 1.12 it connected in five layers, each waiting for its slowest client, 1 + 60 + 15 + 10 + 2 = 88 ms,
and disconnected in reverse in 1 + 1 + 5 + 10 = 17 ms, 105 ms in all: `Repository` and `Users` waited at
the layer barriers for `Kafka` while `Postgres` was long connected.

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
  not either with the nominal 84 ms.

## Findings

The baseline was taken on an Apple M2 Pro, macOS 26.6.2 (arm64), at commit `59883af` of what became `nuke-di`
1.13.0, which still called itself 1.12.0 there: the version in the tables and the JSON,
Python 3.11.7, 3.12.5, 3.13.14 and 3.14.6, each in a fresh `uv` environment from `uv.lock`, with
`N = 10, 100, 1000` and 20 repeats. It is the state after the connect by dependency of 1.13.0
([#28](https://github.com/troyan-dy/nuke-di/issues/28)); the "before" figures are the 1.11.1 baseline at commit
`bd9241f`, in the git history of `docs/benchmarks/`, taken after the performance changes of 1.9.1–1.11.1
([#29](https://github.com/troyan-dy/nuke-di/issues/29), [#30](https://github.com/troyan-dy/nuke-di/issues/30),
[#32](https://github.com/troyan-dy/nuke-di/issues/32), [#33](https://github.com/troyan-dy/nuke-di/issues/33),
[#35](https://github.com/troyan-dy/nuke-di/issues/35), [#36](https://github.com/troyan-dy/nuke-di/issues/36),
[#55](https://github.com/troyan-dy/nuke-di/issues/55)).

- **The application connects and disconnects in its critical path**: 87.2–87.3 ms of wall time against
  87.0–87.1 ms for the coroutines scheduled by hand, 0.09–0.35 ms above it, against 110.5–112.1 ms and
  23.6–24.3 ms (21%) lost at the layer barriers in 1.11.1. `Repository` and `Users` no longer wait for `Kafka`
  once `Postgres` has connected. The figure is the same on every version, because it is the clients' sleeps and
  not the scheduling.
- **A chain of 1000 clients connects and disconnects in 28–36 ms** instead of 98–106 ms, 28–36 µs per client
  against about 0.1 ms per layer: a client of a chain waits for the one below it on an event instead of a task
  group per layer. **Independent clients cost 13–18 µs per client at 100 or more** (23–26 µs at 10; 3.13's
  57 µs at 10 is noise, its p95 is 1.24 ms), against 9–15 µs in 1.11.1, and 1000 of them take 12.8–16.1 ms
  instead of 9.1–12.2 ms: every client now has a task that first looks at what it waits for, and an event that
  its consumers wait on. The clients' own coroutines take 0.15–0.35 µs each, so all of it is scheduling. Not a
  hot spot: a real `connect()` takes milliseconds, a hundred to a thousand times more than its scheduling.
- **`resolve()` costs 3.5–6.3 µs per client** with real type hints on every version and grows linearly:
  100 clients in 0.35–0.54 ms, 1000 in 4.0–5.4 ms, a chain of 1000 in 4.0–4.7 ms, under the default recursion
  limit on every version, against 3.7–6.9 µs per client in 1.11.1.
- **A second container costs 0.75–1.4 µs per client**, with both kinds of hints, against 1.1–1.7 µs in 1.11.1:
  `resolve()` no longer computes a layer per client. The classes resolved once in the process have what their
  `__init__` takes cached, string annotations evaluated, so the container only builds the instances: 3.3–5.3
  times below the first container with real type hints, 85–111 µs for 100 clients and 0.9–1.4 ms for 1000. It
  is what every test of a session pays after the first one. Two samples are outliers of the machine, 4.85 µs
  per client for `wide, strings` at 100 on 3.11 and 2.0 µs for `mixed, strings` at 1000 on 3.13.
- **String annotations cost 1.8–6.1 times the real-type figure on the first container**: 7–33 µs per client.
  The ratio is 1.8–2.4 on 3.11, 2.2–3.7 on 3.12, 2.7–4.4 on 3.13 and 2.1–6.1 on 3.14, and the mixed tree pays
  the most, with two or three hints per class against one: `get_type_hints()` compiles and evaluates every
  string once per class, and the `annotationlib` of 3.14 does more per string. Most code bases have
  `from __future__ import annotations`, so this is the figure their startup pays, and the second container
  above is the one their tests pay.
- **A warm `resolve()`, the singleton cache hit, is 83–117 ns** (one sample of 207 ns on 3.13 is noise): a
  dictionary lookup after the `connected` check, without the lock of
  [#32](https://github.com/troyan-dy/nuke-di/issues/32).
- **`inject()` takes 8–10 µs** to bind a function with two clients. The `partial` it returns adds
  47–87 ns to a call that takes 37–42 ns without it.
- **A `NotSingletonClient` costs 0.2–1.3 µs more per consumer** than the shared instance of a `Client` at 100
  and 1000 consumers: a fresh instance is built from the cached arguments of its class.
- **The test cycle is `resolve()` plus the mock.** The autospec of `mock()` costs 0.65–0.9 ms; `override()`
  with an instance costs the same as a `resolve()` in a second container, 0.10–0.11 ms for 100 clients.
- **A FastAPI handler that takes a client through `nuke-di` costs the same as one with a plain
  `Depends()`**: 106–114 µs per request for both, within 1% of each other, and 4–7 µs above a handler
  without dependencies. The ASGI stack is the cost, not the injection.
- **`import nuke_di` takes 28–39 ms**, about 20 ms of it `asyncio` and 5 ms `logging`, both imported through
  `nuke_di.clients`. `nuke_di.fastapi` adds 4–23 ms on top of `fastapi`'s 148–182 ms, a figure as noisy as a
  fresh interpreter is. This is the figure the lazy-import work in
  [#14](https://github.com/troyan-dy/nuke-di/issues/14) moves.
- **A resolved client takes about 510 bytes**: 505–521 kB for the 1000-client mixed tree, against 575–590 kB
  in 1.11.1, the layer per client gone.
- Between versions, `resolve()` with real type hints is within 25%, and with strings 3.13 and 3.14 are the
  slowest by far; `connect()`, the application and the request are the same on every version.

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
| `startup` and `shutdown` (`--only connect`) | Start and stop clients whose `connect()` and `disconnect()` sleep for the time a real connection takes, through each library's async lifecycle: `async with` / `connect()` of `nuke-di`; for dishka and wireup an async generator per client that awaits `connect()`, yields the client and awaits `disconnect()`, the root got from the async container and `close()`; for dependency-injector the same generator as a `Resource` per client, `init_resources()` and `shutdown_resources()`. For dishka and wireup also with the root's arguments got by `asyncio.gather()` before the root. The trees are fixed: the application of `benchmarks/run.py` and 10 independent clients of 50 ms. injector has no async lifecycle |

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
| Cold start: a container and a tree of 100 clients        | **532 µs**     | 13.2 ms (24.9×) | 21.0 ms (39.4×) | 1.02 ms (1.9×)      | 1.38 ms (2.6×)  |
| Cold start: the same 100 clients with string annotations | 1.26 ms (1.2×) | 14.1 ms (13.1×) | 21.7 ms (20.1×) | **1.08 ms**         | 1.36 ms (1.3×)  |
| A cached root                                            | 93.0 ns (2.5×) | 266 ns (7.1×)   | 96.6 ns (2.6×)  | **37.4 ns**         | 1.19 µs (31.9×) |
| A FastAPI request with a client                          | 106 µs (1.0×)  | **104 µs**      | 214 µs (2.1×)   | 213 µs (2.0×)       | —               |

The comparison was taken on the same machine, Python 3.11.7, with `N = 10, 100, 1000` and 20 repeats,
on `nuke-di` 1.11.1, dishka 1.10.1, wireup 2.12.1, dependency-injector 4.49.1 and injector 0.24.0.

- **A cold tree costs 4–6.5 µs per client in `nuke-di`**, 2–2.6 times less than dependency-injector
  (9–13 µs) and injector (11–17 µs), which, like `nuke-di`, read the signatures and build the tree on demand:
  532 µs for 100 clients against 1.02 and 1.38 ms. dishka (96–186 µs per client) and wireup (183–540 µs)
  validate the whole graph when the container is created: 20–120 times more, 0.1–0.5 s for 1000 clients. That
  is the price of their startup checks, paid once. The 1.9.0 comparison had `nuke-di` at 7–13 µs and
  dependency-injector level with it; it also gave every sample the same classes, which since the per-class cache of 1.9.1 measures
  `nuke-di`'s second container: 158 µs for the 100 clients of the row above, 5.9 times ahead of
  dependency-injector, a startup no application has. The classes made per sample cost dependency-injector
  about 25% more too, injector 5–15% and dishka and wireup a few percent, most likely for the first attribute
  lookups on a new class.
- **With string annotations `nuke-di` costs 8–15 µs per client, 1.7–2.4 times its real-type figure**,
  which levels the cold-start row at 100 clients: dependency-injector (9–13 µs, wired by name, no
  annotation read) is 1.2 times ahead, injector (11–16 µs, the hints evaluated at import) 1.1 times behind.
  dishka and wireup read the strings too and pay at most 17% more for them, a part of their validation.
  The second container of `nuke-di` does not read them again, see the [Findings](#findings).
- **A cached root costs 37 ns–1.2 µs**: 37–39 ns in dependency-injector (Cython), 88–93 ns in `nuke-di`,
  92–101 ns in wireup, 255–272 ns in dishka, and 1.2 µs in injector, which resolves the binding on every
  `get()`.
- **A FastAPI request through `nuke-di` or dishka costs 104–106 µs**, the same as a plain `Depends()`
  (103–107 µs in the baseline above). Through wireup and dependency-injector it costs 213–214 µs, twice
  that: their integrations do more per request, as their documentation wires them; what exactly is not
  investigated here.
- A chain of 1000 clients exceeds the default recursion limit in dishka, wireup and injector, and in
  dependency-injector on 3.11 (not on 3.14); `nuke-di` resolves it without recursion since
  [#35](https://github.com/troyan-dy/nuke-di/issues/35). The runner raises the limit, which is enough for every
  library but injector.

### Slow connections

The figures above are what a library costs; with real connections the cost is the waiting, and what decides
the startup is when a library starts each `connect()`. `compare.py --only connect` starts and stops the
application tree of the [Findings](#findings), 8 clients whose `connect()` takes 1–60 ms, which needs 68 ms
along its longest chain to start and 16 ms to stop, and a root with 10 independent clients of 50 ms each:

```console
$ uv run python benchmarks/compare.py --only connect --summary
nuke-di 1.14.2 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 8d0700b · N = 10, 100, 1000 · 20 repeats
nuke-di 1.14.2 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

| Lower is better                                     | nuke-di     | dishka         | wireup         | dependency-injector | injector |
|-----------------------------------------------------|------------:|---------------:|---------------:|--------------------:|---------:|
| Startup: 8 clients, connect() of 1–60 ms            | **70.8 ms** | 156 ms (2.2×)  | 156 ms (2.2×)  | 71.1 ms (1.0×)      | —        |
| Shutdown: the same 8 clients                        | **18.7 ms** | 30.1 ms (1.6×) | 29.8 ms (1.6×) | 26.0 ms (1.4×)      | —        |
| Startup: 10 independent clients, connect() of 50 ms | **52.3 ms** | 520 ms (10.0×) | 522 ms (10.0×) | 52.5 ms (1.0×)      | —        |
```

- **dishka and wireup connect one client at a time within a `get()`**, in the order they walk the graph: an
  async factory awaits the factories of its arguments one after the other, so `Kafka` (60 ms) starts only after
  `Postgres`, `Repository`, `Redis` and `Users` are connected. The startup is the sum of every `connect()`,
  143 ms for the application and 500 ms for the 10 clients, and `close()` disconnects in reverse, the sum
  again: 25 ms and 50 ms. Ten clients of 50 ms take ten times as long as in `nuke-di`.
- **wireup connects concurrently what the application gets concurrently.** With the root's arguments got by
  `asyncio.gather()` by hand before the root (the `startup, the root's arguments gathered by hand` rows below),
  the 10 clients start in 54.8 ms and the application in 86.9 ms: its two branches run side by side, but the
  chain under `Users` still connects one client after another, 76 ms where its longest chain is 56 ms. The
  gather is written for a tree the application knows, and a dependency added to a client does not join it.
  dishka serializes its `get()` calls with a lock and takes as long gathered as not: 156 and 521 ms.
- **dependency-injector starts as concurrently as `nuke-di`** once every client is a `Resource`:
  `init_resources()` gathers all of them, and a resource awaits only its own arguments, so the startup is
  the longest chain, 71.1 ms against 70.8 ms. Its shutdown goes in layers, first the resources that no
  initialized resource depends on, and every layer waits for its slowest, 21 ms for the application:
  26.0 ms against 18.7 ms, where the longest chain is 16 ms. The `Resource` is a generator written for every
  client, and its arguments are named by hand; a `Singleton` of the class does not await `connect()` at all.
- **None of the three connects anything when its container is created.** dishka and wireup call a
  factory on the first `get()` of the client or of a client that needs it, and dependency-injector on the
  first call of a `Resource` that `init_resources()` did not initialize; `tests/test_benchmarks.py` checks it on
  every run. An application that does not get the root at startup, as the startup figures above do, connects
  inside its first request, which waits for every `connect()` on its path and fails if one of them fails; the
  process is up and ready by then. dishka and wireup do check the graph when the container is created, so a
  missing dependency fails the startup, but a connection that fails does not. `nuke-di` connects every
  resolved client in `async with DI` (or `Dependencies.connect()`), and a client that cannot connect stops
  the startup.
- **injector has no async lifecycle**: it builds objects synchronously, and awaiting a `connect()` is left
  to the application.

The same run with every sample, on Python 3.11.7 and the machine of the baseline, at commit `8d0700b`:

| Library             | Scenario                                       | Shape                                        | N |             Median |     p95 | Per client |
|---------------------|------------------------------------------------|----------------------------------------------|--:|-------------------:|--------:|-----------:|
| nuke-di             | startup: connect() of every client             | application: 8 clients, connect() of 1–60 ms |   |            70.8 ms | 71.5 ms |            |
| nuke-di             | shutdown: disconnect() of every client         | application: 8 clients, connect() of 1–60 ms |   |            18.7 ms | 18.7 ms |            |
| dishka              | startup: connect() of every client             | application: 8 clients, connect() of 1–60 ms |   |             156 ms |  157 ms |            |
| dishka              | shutdown: disconnect() of every client         | application: 8 clients, connect() of 1–60 ms |   |            30.1 ms | 31.0 ms |            |
| dishka              | startup, the root's arguments gathered by hand | application: 8 clients, connect() of 1–60 ms |   |             156 ms |  157 ms |            |
| wireup              | startup: connect() of every client             | application: 8 clients, connect() of 1–60 ms |   |             156 ms |  157 ms |            |
| wireup              | shutdown: disconnect() of every client         | application: 8 clients, connect() of 1–60 ms |   |            29.8 ms | 31.0 ms |            |
| wireup              | startup, the root's arguments gathered by hand | application: 8 clients, connect() of 1–60 ms |   |            86.9 ms | 88.3 ms |            |
| dependency-injector | startup: connect() of every client             | application: 8 clients, connect() of 1–60 ms |   |            71.1 ms | 71.9 ms |            |
| dependency-injector | shutdown: disconnect() of every client         | application: 8 clients, connect() of 1–60 ms |   |            26.0 ms | 26.2 ms |            |
| injector            | startup: connect() of every client             | application: 8 clients, connect() of 1–60 ms |   | no async lifecycle |         |            |
| injector            | shutdown: disconnect() of every client         | application: 8 clients, connect() of 1–60 ms |   | no async lifecycle |         |            |
| nuke-di             | startup: connect() of every client             | wide: 10 clients, connect() of 50 ms         |   |            52.3 ms | 52.3 ms |            |
| nuke-di             | shutdown: disconnect() of every client         | wide: 10 clients, connect() of 50 ms         |   |            6.44 ms | 6.47 ms |            |
| dishka              | startup: connect() of every client             | wide: 10 clients, connect() of 50 ms         |   |             520 ms |  522 ms |            |
| dishka              | shutdown: disconnect() of every client         | wide: 10 clients, connect() of 50 ms         |   |            62.4 ms | 62.9 ms |            |
| dishka              | startup, the root's arguments gathered by hand | wide: 10 clients, connect() of 50 ms         |   |             521 ms |  524 ms |            |
| wireup              | startup: connect() of every client             | wide: 10 clients, connect() of 50 ms         |   |             522 ms |  523 ms |            |
| wireup              | shutdown: disconnect() of every client         | wide: 10 clients, connect() of 50 ms         |   |            61.9 ms | 62.7 ms |            |
| wireup              | startup, the root's arguments gathered by hand | wide: 10 clients, connect() of 50 ms         |   |            54.8 ms | 55.2 ms |            |
| dependency-injector | startup: connect() of every client             | wide: 10 clients, connect() of 50 ms         |   |            52.5 ms | 52.6 ms |            |
| dependency-injector | shutdown: disconnect() of every client         | wide: 10 clients, connect() of 50 ms         |   |            6.72 ms | 6.90 ms |            |
| injector            | startup: connect() of every client             | wide: 10 clients, connect() of 50 ms         |   | no async lifecycle |         |            |
| injector            | shutdown: disconnect() of every client         | wide: 10 clients, connect() of 50 ms         |   | no async lifecycle |         |            |

### Python 3.11.7, nuke-di 1.11.1 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

nuke-di 1.11.1 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit bd9241f · N = 10, 100, 1000 · 20 repeats

| Library             | Scenario                             | Shape          |    N |         Median |     p95 | Per client |
|---------------------|--------------------------------------|----------------|-----:|---------------:|--------:|-----------:|
| nuke-di             | cold: container, registration, root  | wide           |   10 |        53.0 µs | 63.1 µs |    5.30 µs |
| dishka              | cold: container, registration, root  | wide           |   10 |        1.53 ms | 1.73 ms |     153 µs |
| wireup              | cold: container, registration, root  | wide           |   10 |        2.29 ms | 2.49 ms |     229 µs |
| dependency-injector | cold: container, registration, root  | wide           |   10 |         100 µs |  136 µs |    10.0 µs |
| injector            | cold: container, registration, root  | wide           |   10 |         131 µs |  184 µs |    13.1 µs |
| nuke-di             | cold: container, registration, root  | wide           |  100 |         465 µs |  575 µs |    4.65 µs |
| dishka              | cold: container, registration, root  | wide           |  100 |        10.0 ms | 14.4 ms |     100 µs |
| wireup              | cold: container, registration, root  | wide           |  100 |        18.3 ms | 21.5 ms |     183 µs |
| dependency-injector | cold: container, registration, root  | wide           |  100 |         896 µs |  947 µs |    8.96 µs |
| injector            | cold: container, registration, root  | wide           |  100 |        1.09 ms | 1.19 ms |    10.9 µs |
| nuke-di             | cold: container, registration, root  | wide           | 1000 |        4.22 ms | 4.99 ms |    4.22 µs |
| dishka              | cold: container, registration, root  | wide           | 1000 |        96.2 ms |  116 ms |    96.2 µs |
| wireup              | cold: container, registration, root  | wide           | 1000 |         202 ms |  220 ms |     202 µs |
| dependency-injector | cold: container, registration, root  | wide           | 1000 |        13.1 ms | 14.0 ms |    13.1 µs |
| injector            | cold: container, registration, root  | wide           | 1000 |        11.7 ms | 13.0 ms |    11.7 µs |
| nuke-di             | cold: container, registration, root  | deep           |   10 |        54.0 µs | 71.7 µs |    5.40 µs |
| dishka              | cold: container, registration, root  | deep           |   10 |        1.72 ms | 1.86 ms |     172 µs |
| wireup              | cold: container, registration, root  | deep           |   10 |        2.33 ms | 2.42 ms |     233 µs |
| dependency-injector | cold: container, registration, root  | deep           |   10 |         106 µs |  124 µs |    10.6 µs |
| injector            | cold: container, registration, root  | deep           |   10 |         150 µs |  172 µs |    15.0 µs |
| nuke-di             | cold: container, registration, root  | deep           |  100 |         437 µs |  483 µs |    4.37 µs |
| dishka              | cold: container, registration, root  | deep           |  100 |        13.4 ms | 17.5 ms |     134 µs |
| wireup              | cold: container, registration, root  | deep           |  100 |        24.3 ms | 40.8 ms |     243 µs |
| dependency-injector | cold: container, registration, root  | deep           |  100 |         924 µs |  976 µs |    9.24 µs |
| injector            | cold: container, registration, root  | deep           |  100 |        1.32 ms | 1.36 ms |    13.2 µs |
| nuke-di             | cold: container, registration, root  | deep           | 1000 |        4.47 ms | 5.25 ms |    4.47 µs |
| dishka              | cold: container, registration, root  | deep           | 1000 |         144 ms |  162 ms |     144 µs |
| wireup              | cold: container, registration, root  | deep           | 1000 |         540 ms |  598 ms |     540 µs |
| dependency-injector | cold: container, registration, root  | deep           | 1000 |        10.3 ms | 12.3 ms |    10.3 µs |
| injector            | cold: container, registration, root  | deep           | 1000 | RecursionError |         |            |
| nuke-di             | cold: container, registration, root  | mixed          |   10 |        64.8 µs | 84.4 µs |    6.48 µs |
| dishka              | cold: container, registration, root  | mixed          |   10 |        1.86 ms | 2.37 ms |     186 µs |
| wireup              | cold: container, registration, root  | mixed          |   10 |        2.41 ms | 2.76 ms |     241 µs |
| dependency-injector | cold: container, registration, root  | mixed          |   10 |         125 µs |  168 µs |    12.5 µs |
| injector            | cold: container, registration, root  | mixed          |   10 |         159 µs |  170 µs |    15.9 µs |
| nuke-di             | cold: container, registration, root  | mixed          |  100 |         532 µs |  656 µs |    5.32 µs |
| dishka              | cold: container, registration, root  | mixed          |  100 |        13.2 ms | 15.4 ms |     132 µs |
| wireup              | cold: container, registration, root  | mixed          |  100 |        21.0 ms | 26.8 ms |     210 µs |
| dependency-injector | cold: container, registration, root  | mixed          |  100 |        1.02 ms | 1.13 ms |    10.2 µs |
| injector            | cold: container, registration, root  | mixed          |  100 |        1.38 ms | 1.57 ms |    13.8 µs |
| nuke-di             | cold: container, registration, root  | mixed          | 1000 |        4.61 ms | 5.67 ms |    4.61 µs |
| dishka              | cold: container, registration, root  | mixed          | 1000 |         106 ms |  112 ms |     106 µs |
| wireup              | cold: container, registration, root  | mixed          | 1000 |         241 ms |  282 ms |     241 µs |
| dependency-injector | cold: container, registration, root  | mixed          | 1000 |        10.8 ms | 11.8 ms |    10.8 µs |
| injector            | cold: container, registration, root  | mixed          | 1000 |        16.8 ms | 18.0 ms |    16.8 µs |
| nuke-di             | cold: container, registration, root  | wide, strings  |   10 |        90.4 µs |  104 µs |    9.04 µs |
| dishka              | cold: container, registration, root  | wide, strings  |   10 |        1.46 ms | 1.60 ms |     146 µs |
| wireup              | cold: container, registration, root  | wide, strings  |   10 |        2.20 ms | 2.61 ms |     220 µs |
| dependency-injector | cold: container, registration, root  | wide, strings  |   10 |        94.0 µs | 98.5 µs |    9.40 µs |
| injector            | cold: container, registration, root  | wide, strings  |   10 |         127 µs |  131 µs |    12.7 µs |
| nuke-di             | cold: container, registration, root  | wide, strings  |  100 |         798 µs | 1000 µs |    7.98 µs |
| dishka              | cold: container, registration, root  | wide, strings  |  100 |        10.8 ms | 33.4 ms |     108 µs |
| wireup              | cold: container, registration, root  | wide, strings  |  100 |        19.1 ms | 23.2 ms |     191 µs |
| dependency-injector | cold: container, registration, root  | wide, strings  |  100 |         895 µs | 1.12 ms |    8.95 µs |
| injector            | cold: container, registration, root  | wide, strings  |  100 |        1.09 ms | 1.19 ms |    10.9 µs |
| nuke-di             | cold: container, registration, root  | wide, strings  | 1000 |        7.96 ms | 9.14 ms |    7.96 µs |
| dishka              | cold: container, registration, root  | wide, strings  | 1000 |        97.7 ms |  103 ms |    97.7 µs |
| wireup              | cold: container, registration, root  | wide, strings  | 1000 |         214 ms |  219 ms |     214 µs |
| dependency-injector | cold: container, registration, root  | wide, strings  | 1000 |        13.0 ms | 14.1 ms |    13.0 µs |
| injector            | cold: container, registration, root  | wide, strings  | 1000 |        11.0 ms | 12.7 ms |    11.0 µs |
| nuke-di             | cold: container, registration, root  | deep, strings  |   10 |        91.5 µs |  107 µs |    9.15 µs |
| dishka              | cold: container, registration, root  | deep, strings  |   10 |        1.67 ms | 1.91 ms |     167 µs |
| wireup              | cold: container, registration, root  | deep, strings  |   10 |        2.27 ms | 2.60 ms |     227 µs |
| dependency-injector | cold: container, registration, root  | deep, strings  |   10 |         101 µs |  114 µs |    10.1 µs |
| injector            | cold: container, registration, root  | deep, strings  |   10 |         155 µs |  199 µs |    15.5 µs |
| nuke-di             | cold: container, registration, root  | deep, strings  |  100 |         872 µs | 1.02 ms |    8.72 µs |
| dishka              | cold: container, registration, root  | deep, strings  |  100 |        12.8 ms | 13.7 ms |     128 µs |
| wireup              | cold: container, registration, root  | deep, strings  |  100 |        23.3 ms | 24.0 ms |     233 µs |
| dependency-injector | cold: container, registration, root  | deep, strings  |  100 |         927 µs |  960 µs |    9.27 µs |
| injector            | cold: container, registration, root  | deep, strings  |  100 |        1.47 ms | 1.81 ms |    14.7 µs |
| nuke-di             | cold: container, registration, root  | deep, strings  | 1000 |        8.95 ms | 9.86 ms |    8.95 µs |
| dishka              | cold: container, registration, root  | deep, strings  | 1000 |         147 ms |  155 ms |     147 µs |
| wireup              | cold: container, registration, root  | deep, strings  | 1000 |         590 ms |  628 ms |     590 µs |
| dependency-injector | cold: container, registration, root  | deep, strings  | 1000 |        10.5 ms | 11.1 ms |    10.5 µs |
| injector            | cold: container, registration, root  | deep, strings  | 1000 | RecursionError |         |            |
| nuke-di             | cold: container, registration, root  | mixed, strings |   10 |         148 µs |  169 µs |    14.8 µs |
| dishka              | cold: container, registration, root  | mixed, strings |   10 |        1.88 ms | 2.21 ms |     188 µs |
| wireup              | cold: container, registration, root  | mixed, strings |   10 |        2.38 ms | 2.89 ms |     238 µs |
| dependency-injector | cold: container, registration, root  | mixed, strings |   10 |         114 µs |  118 µs |    11.4 µs |
| injector            | cold: container, registration, root  | mixed, strings |   10 |         162 µs |  188 µs |    16.2 µs |
| nuke-di             | cold: container, registration, root  | mixed, strings |  100 |        1.26 ms | 1.43 ms |    12.6 µs |
| dishka              | cold: container, registration, root  | mixed, strings |  100 |        14.1 ms | 15.0 ms |     141 µs |
| wireup              | cold: container, registration, root  | mixed, strings |  100 |        21.7 ms | 23.0 ms |     217 µs |
| dependency-injector | cold: container, registration, root  | mixed, strings |  100 |        1.08 ms | 1.18 ms |    10.8 µs |
| injector            | cold: container, registration, root  | mixed, strings |  100 |        1.36 ms | 1.67 ms |    13.6 µs |
| nuke-di             | cold: container, registration, root  | mixed, strings | 1000 |        11.0 ms | 12.0 ms |    11.0 µs |
| dishka              | cold: container, registration, root  | mixed, strings | 1000 |         123 ms |  129 ms |     123 µs |
| wireup              | cold: container, registration, root  | mixed, strings | 1000 |         270 ms |  277 ms |     270 µs |
| dependency-injector | cold: container, registration, root  | mixed, strings | 1000 |        10.9 ms | 13.9 ms |    10.9 µs |
| injector            | cold: container, registration, root  | mixed, strings | 1000 |        16.2 ms | 17.6 ms |    16.2 µs |
| nuke-di             | warm: the root again                 | wide           |   10 |        90.1 ns | 93.6 ns |            |
| dishka              | warm: the root again                 | wide           |   10 |         255 ns |  272 ns |            |
| wireup              | warm: the root again                 | wide           |   10 |        92.9 ns |  101 ns |            |
| dependency-injector | warm: the root again                 | wide           |   10 |        37.1 ns | 41.9 ns |            |
| injector            | warm: the root again                 | wide           |   10 |        1.20 µs | 1.24 µs |            |
| nuke-di             | warm: the root again                 | wide           |  100 |        87.9 ns | 95.2 ns |            |
| dishka              | warm: the root again                 | wide           |  100 |         264 ns |  293 ns |            |
| wireup              | warm: the root again                 | wide           |  100 |        92.8 ns |  101 ns |            |
| dependency-injector | warm: the root again                 | wide           |  100 |        37.1 ns | 37.5 ns |            |
| injector            | warm: the root again                 | wide           |  100 |        1.19 µs | 1.24 µs |            |
| nuke-di             | warm: the root again                 | wide           | 1000 |        88.0 ns | 97.5 ns |            |
| dishka              | warm: the root again                 | wide           | 1000 |         264 ns |  299 ns |            |
| wireup              | warm: the root again                 | wide           | 1000 |        98.7 ns |  112 ns |            |
| dependency-injector | warm: the root again                 | wide           | 1000 |        37.1 ns | 42.2 ns |            |
| injector            | warm: the root again                 | wide           | 1000 |        1.23 µs | 1.34 µs |            |
| nuke-di             | warm: the root again                 | deep           |   10 |        89.3 ns | 96.2 ns |            |
| dishka              | warm: the root again                 | deep           |   10 |         269 ns |  370 ns |            |
| wireup              | warm: the root again                 | deep           |   10 |        98.7 ns |  127 ns |            |
| dependency-injector | warm: the root again                 | deep           |   10 |        37.4 ns | 44.9 ns |            |
| injector            | warm: the root again                 | deep           |   10 |        1.20 µs | 1.26 µs |            |
| nuke-di             | warm: the root again                 | deep           |  100 |        89.8 ns |  103 ns |            |
| dishka              | warm: the root again                 | deep           |  100 |         261 ns |  296 ns |            |
| wireup              | warm: the root again                 | deep           |  100 |         101 ns |  110 ns |            |
| dependency-injector | warm: the root again                 | deep           |  100 |        38.8 ns | 45.5 ns |            |
| injector            | warm: the root again                 | deep           |  100 |        1.19 µs | 1.22 µs |            |
| nuke-di             | warm: the root again                 | deep           | 1000 |        89.5 ns | 96.5 ns |            |
| dishka              | warm: the root again                 | deep           | 1000 |         272 ns |  381 ns |            |
| wireup              | warm: the root again                 | deep           | 1000 |        92.0 ns | 98.9 ns |            |
| dependency-injector | warm: the root again                 | deep           | 1000 |        36.6 ns | 43.4 ns |            |
| injector            | warm: the root again                 | deep           | 1000 | RecursionError |         |            |
| nuke-di             | warm: the root again                 | mixed          |   10 |        88.7 ns |  102 ns |            |
| dishka              | warm: the root again                 | mixed          |   10 |         265 ns |  329 ns |            |
| wireup              | warm: the root again                 | mixed          |   10 |        95.1 ns |  107 ns |            |
| dependency-injector | warm: the root again                 | mixed          |   10 |        37.1 ns | 41.1 ns |            |
| injector            | warm: the root again                 | mixed          |   10 |        1.19 µs | 1.21 µs |            |
| nuke-di             | warm: the root again                 | mixed          |  100 |        93.0 ns |  104 ns |            |
| dishka              | warm: the root again                 | mixed          |  100 |         266 ns |  277 ns |            |
| wireup              | warm: the root again                 | mixed          |  100 |        96.6 ns |  114 ns |            |
| dependency-injector | warm: the root again                 | mixed          |  100 |        37.4 ns | 47.9 ns |            |
| injector            | warm: the root again                 | mixed          |  100 |        1.19 µs | 1.27 µs |            |
| nuke-di             | warm: the root again                 | mixed          | 1000 |        87.6 ns | 94.8 ns |            |
| dishka              | warm: the root again                 | mixed          | 1000 |         258 ns |  270 ns |            |
| wireup              | warm: the root again                 | mixed          | 1000 |        94.2 ns |  102 ns |            |
| dependency-injector | warm: the root again                 | mixed          | 1000 |        37.2 ns | 45.3 ns |            |
| injector            | warm: the root again                 | mixed          | 1000 |        1.22 µs | 1.36 µs |            |
| nuke-di             | one request, a client in the handler | FastAPI        |      |         106 µs |  113 µs |            |
| dishka              | one request, a client in the handler | FastAPI        |      |         104 µs |  111 µs |            |
| wireup              | one request, a client in the handler | FastAPI        |      |         214 µs |  286 µs |            |
| dependency-injector | one request, a client in the handler | FastAPI        |      |         213 µs |  218 µs |            |

## Baseline

### Python 3.11.7

nuke-di 1.12.0 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 59883af · N = 10, 100, 1000 · 20 repeats

| Scenario                                                     | Shape                                        |    N |  Median |     p95 | Per client |
|--------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                              | wide                                         |   10 | 42.8 µs | 48.6 µs |    4.28 µs |
| resolve(), second container, classes seen before             | wide                                         |   10 | 8.98 µs | 15.4 µs |     898 ns |
| resolve(), warm                                              | wide                                         |   10 | 86.5 ns | 93.0 ns |            |
| resolve(), cold                                              | wide                                         |  100 |  380 µs |  468 µs |    3.80 µs |
| resolve(), second container, classes seen before             | wide                                         |  100 |  111 µs |  561 µs |    1.11 µs |
| resolve(), warm                                              | wide                                         |  100 | 94.1 ns |  302 ns |            |
| resolve(), cold                                              | wide                                         | 1000 | 4.05 ms | 6.54 ms |    4.05 µs |
| resolve(), second container, classes seen before             | wide                                         | 1000 | 1.18 ms | 1.62 ms |    1.18 µs |
| resolve(), warm                                              | wide                                         | 1000 | 85.9 ns | 86.5 ns |            |
| resolve(), cold                                              | deep                                         |   10 | 39.8 µs | 49.0 µs |    3.98 µs |
| resolve(), second container, classes seen before             | deep                                         |   10 | 8.98 µs | 9.56 µs |     898 ns |
| resolve(), warm                                              | deep                                         |   10 | 89.6 ns | 95.5 ns |            |
| resolve(), cold                                              | deep                                         |  100 |  376 µs |  454 µs |    3.76 µs |
| resolve(), second container, classes seen before             | deep                                         |  100 | 94.6 µs |  104 µs |     946 ns |
| resolve(), warm                                              | deep                                         |  100 | 84.8 ns | 88.9 ns |            |
| resolve(), cold                                              | deep                                         | 1000 | 3.95 ms | 5.10 ms |    3.95 µs |
| resolve(), second container, classes seen before             | deep                                         | 1000 |  979 µs | 1.09 ms |     979 ns |
| resolve(), warm                                              | deep                                         | 1000 | 90.3 ns | 99.8 ns |            |
| resolve(), cold                                              | mixed                                        |   10 | 51.8 µs | 64.8 µs |    5.18 µs |
| resolve(), second container, classes seen before             | mixed                                        |   10 | 10.3 µs | 13.5 µs |    1.03 µs |
| resolve(), warm                                              | mixed                                        |   10 | 93.0 ns |  105 ns |            |
| resolve(), cold                                              | mixed                                        |  100 |  476 µs |  620 µs |    4.76 µs |
| resolve(), second container, classes seen before             | mixed                                        |  100 |  111 µs |  191 µs |    1.11 µs |
| resolve(), warm                                              | mixed                                        |  100 | 85.2 ns | 90.9 ns |            |
| resolve(), cold                                              | mixed                                        | 1000 | 4.46 ms | 5.31 ms |    4.46 µs |
| resolve(), second container, classes seen before             | mixed                                        | 1000 | 1.37 ms | 2.04 ms |    1.37 µs |
| resolve(), warm                                              | mixed                                        | 1000 | 89.3 ns | 97.3 ns |            |
| resolve(), cold                                              | wide, strings                                |   10 | 77.2 µs |  112 µs |    7.72 µs |
| resolve(), second container, classes seen before             | wide, strings                                |   10 | 9.21 µs | 28.2 µs |     921 ns |
| resolve(), cold                                              | wide, strings                                |  100 |  811 µs | 2.09 ms |    8.11 µs |
| resolve(), second container, classes seen before             | wide, strings                                |  100 |  485 µs |  804 µs |    4.85 µs |
| resolve(), cold                                              | wide, strings                                | 1000 | 7.09 ms | 8.49 ms |    7.09 µs |
| resolve(), second container, classes seen before             | wide, strings                                | 1000 | 1.26 ms | 1.53 ms |    1.26 µs |
| resolve(), cold                                              | deep, strings                                |   10 | 84.0 µs |  107 µs |    8.40 µs |
| resolve(), second container, classes seen before             | deep, strings                                |   10 | 8.63 µs | 12.1 µs |     863 ns |
| resolve(), cold                                              | deep, strings                                |  100 |  739 µs |  941 µs |    7.39 µs |
| resolve(), second container, classes seen before             | deep, strings                                |  100 |  102 µs |  147 µs |    1.02 µs |
| resolve(), cold                                              | deep, strings                                | 1000 | 7.17 ms | 7.90 ms |    7.17 µs |
| resolve(), second container, classes seen before             | deep, strings                                | 1000 |  976 µs | 1.06 ms |     976 ns |
| resolve(), cold                                              | mixed, strings                               |   10 |  122 µs |  148 µs |    12.2 µs |
| resolve(), second container, classes seen before             | mixed, strings                               |   10 | 10.6 µs | 18.3 µs |    1.06 µs |
| resolve(), cold                                              | mixed, strings                               |  100 | 1.13 ms | 1.31 ms |    11.3 µs |
| resolve(), second container, classes seen before             | mixed, strings                               |  100 | 98.7 µs |  109 µs |     987 ns |
| resolve(), cold                                              | mixed, strings                               | 1000 | 10.3 ms | 11.8 ms |    10.3 µs |
| resolve(), second container, classes seen before             | mixed, strings                               | 1000 | 1.07 ms | 1.21 ms |    1.07 µs |
| connect() + disconnect()                                     | wide: N independent clients                  |   10 |  248 µs |  284 µs |    24.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  |   10 | 2.19 µs | 2.57 µs |     219 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  |   10 |  246 µs |  281 µs |    24.6 µs |
| connect() + disconnect()                                     | wide: N independent clients                  |  100 | 1.59 ms | 1.87 ms |    15.9 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  |  100 | 18.9 µs |  109 µs |     189 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  |  100 | 1.57 ms | 1.84 ms |    15.7 µs |
| connect() + disconnect()                                     | wide: N independent clients                  | 1000 | 16.1 ms | 30.0 ms |    16.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  | 1000 |  353 µs |  837 µs |     353 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  | 1000 | 15.7 ms | 29.2 ms |    15.7 µs |
| connect() + disconnect()                                     | deep: a chain of N                           |   10 |  471 µs |  707 µs |    47.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           |   10 | 2.87 µs | 4.90 µs |     287 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           |   10 |  468 µs |  704 µs |    46.8 µs |
| connect() + disconnect()                                     | deep: a chain of N                           |  100 | 3.46 ms | 3.77 ms |    34.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           |  100 | 21.1 µs | 32.9 µs |     211 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           |  100 | 3.44 ms | 3.75 ms |    34.4 µs |
| connect() + disconnect()                                     | deep: a chain of N                           | 1000 | 35.9 ms | 59.9 ms |    35.9 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           | 1000 |  354 µs |  761 µs |     354 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           | 1000 | 35.5 ms | 59.5 ms |    35.5 µs |
| connect() + disconnect(), wall time                          | application: 8 clients, connect() of 1–60 ms |    8 | 87.3 ms | 88.4 ms |            |
| connect() + disconnect(), ideal: the critical path           | application: 8 clients, connect() of 1–60 ms |    8 | 87.1 ms | 89.4 ms |            |
| connect() + disconnect(), above the critical path            | application: 8 clients, connect() of 1–60 ms |    8 |  347 µs | 1.39 ms |            |
| inject(), clients resolved                                   | 2 clients                                    |      | 8.32 µs | 8.92 µs |            |
| call of the injected function                                | 2 clients                                    |      | 84.8 ns | 88.3 ns |            |
| call of the plain function                                   | 2 clients                                    |      | 37.3 ns | 38.8 ns |            |
| resolve(), cold                                              | N consumers of a Client                      |   10 | 60.8 µs | 99.2 µs |    6.08 µs |
| resolve(), cold                                              | N consumers of a Client                      |  100 |  511 µs |  656 µs |    5.11 µs |
| resolve(), cold                                              | N consumers of a Client                      | 1000 | 5.17 ms | 5.82 ms |    5.17 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          |   10 | 66.6 µs | 94.1 µs |    6.66 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          |  100 |  587 µs |  806 µs |    5.87 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          | 1000 | 5.90 ms | 6.97 ms |    5.90 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       |   10 |  654 µs |  710 µs |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       |   10 | 13.6 µs | 15.7 µs |    1.36 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       |  100 |  749 µs |  980 µs |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       |  100 |  101 µs |  170 µs |    1.01 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       | 1000 | 1.92 ms | 2.31 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       | 1000 | 1.09 ms | 1.56 ms |    1.09 µs |
| one request                                                  | a client through nuke-di                     |      |  108 µs |  115 µs |            |
| one request                                                  | a plain FastAPI Depends()                    |      |  107 µs |  108 µs |            |
| one request                                                  | no dependencies                              |      |  103 µs |  107 µs |            |
| import, fresh interpreter                                    | nuke_di                                      |      | 28.5 ms | 32.2 ms |            |
| import, fresh interpreter                                    | nuke_di.fastapi                              |      |  152 ms |  159 ms |            |
| import, fresh interpreter                                    | fastapi                                      |      |  148 ms |  175 ms |            |
| resolve(), tracemalloc peak                                  | mixed                                        | 1000 |  513 kB |  590 kB |      513 B |

### Python 3.12.5

nuke-di 1.12.0 · CPython 3.12.5 · macOS-26.6.2-arm64-arm-64bit · commit 59883af · N = 10, 100, 1000 · 20 repeats

| Scenario                                                     | Shape                                        |    N |  Median |     p95 | Per client |
|--------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                              | wide                                         |   10 | 45.6 µs | 54.7 µs |    4.56 µs |
| resolve(), second container, classes seen before             | wide                                         |   10 | 8.46 µs | 12.5 µs |     846 ns |
| resolve(), warm                                              | wide                                         |   10 |  117 ns |  132 ns |            |
| resolve(), cold                                              | wide                                         |  100 |  351 µs |  423 µs |    3.51 µs |
| resolve(), second container, classes seen before             | wide                                         |  100 | 86.5 µs |  105 µs |     865 ns |
| resolve(), warm                                              | wide                                         |  100 |  104 ns |  108 ns |            |
| resolve(), cold                                              | wide                                         | 1000 | 4.03 ms | 4.97 ms |    4.03 µs |
| resolve(), second container, classes seen before             | wide                                         | 1000 | 1.14 ms | 1.33 ms |    1.14 µs |
| resolve(), warm                                              | wide                                         | 1000 |  115 ns |  125 ns |            |
| resolve(), cold                                              | deep                                         |   10 | 37.4 µs | 46.0 µs |    3.74 µs |
| resolve(), second container, classes seen before             | deep                                         |   10 | 8.79 µs | 10.3 µs |     879 ns |
| resolve(), warm                                              | deep                                         |   10 |  117 ns |  119 ns |            |
| resolve(), cold                                              | deep                                         |  100 |  354 µs |  438 µs |    3.54 µs |
| resolve(), second container, classes seen before             | deep                                         |  100 | 95.7 µs |  105 µs |     957 ns |
| resolve(), warm                                              | deep                                         |  100 |  114 ns |  115 ns |            |
| resolve(), cold                                              | deep                                         | 1000 | 4.00 ms | 4.51 ms |    4.00 µs |
| resolve(), second container, classes seen before             | deep                                         | 1000 |  938 µs | 1.19 ms |     938 ns |
| resolve(), warm                                              | deep                                         | 1000 |  111 ns |  117 ns |            |
| resolve(), cold                                              | mixed                                        |   10 | 50.2 µs | 51.3 µs |    5.02 µs |
| resolve(), second container, classes seen before             | mixed                                        |   10 | 9.94 µs | 11.0 µs |     994 ns |
| resolve(), warm                                              | mixed                                        |   10 |  116 ns |  124 ns |            |
| resolve(), cold                                              | mixed                                        |  100 |  460 µs |  662 µs |    4.60 µs |
| resolve(), second container, classes seen before             | mixed                                        |  100 | 93.6 µs | 96.5 µs |     936 ns |
| resolve(), warm                                              | mixed                                        |  100 |  105 ns |  110 ns |            |
| resolve(), cold                                              | mixed                                        | 1000 | 4.52 ms | 4.88 ms |    4.52 µs |
| resolve(), second container, classes seen before             | mixed                                        | 1000 | 1.00 ms | 1.13 ms |    1.00 µs |
| resolve(), warm                                              | mixed                                        | 1000 |  109 ns |  110 ns |            |
| resolve(), cold                                              | wide, strings                                |   10 |  102 µs |  121 µs |    10.2 µs |
| resolve(), second container, classes seen before             | wide, strings                                |   10 | 8.12 µs | 8.70 µs |     812 ns |
| resolve(), cold                                              | wide, strings                                |  100 |  914 µs | 1.17 ms |    9.14 µs |
| resolve(), second container, classes seen before             | wide, strings                                |  100 | 87.3 µs | 92.5 µs |     873 ns |
| resolve(), cold                                              | wide, strings                                | 1000 | 10.1 ms | 14.2 ms |    10.1 µs |
| resolve(), second container, classes seen before             | wide, strings                                | 1000 | 1.15 ms | 1.45 ms |    1.15 µs |
| resolve(), cold                                              | deep, strings                                |   10 | 97.5 µs |  106 µs |    9.75 µs |
| resolve(), second container, classes seen before             | deep, strings                                |   10 | 9.10 µs | 11.0 µs |     910 ns |
| resolve(), cold                                              | deep, strings                                |  100 |  981 µs | 2.10 ms |    9.81 µs |
| resolve(), second container, classes seen before             | deep, strings                                |  100 | 93.2 µs |  102 µs |     932 ns |
| resolve(), cold                                              | deep, strings                                | 1000 | 10.5 ms | 13.4 ms |    10.5 µs |
| resolve(), second container, classes seen before             | deep, strings                                | 1000 |  955 µs | 1.24 ms |     955 ns |
| resolve(), cold                                              | mixed, strings                               |   10 |  172 µs |  286 µs |    17.2 µs |
| resolve(), second container, classes seen before             | mixed, strings                               |   10 | 10.0 µs | 12.2 µs |    1.00 µs |
| resolve(), cold                                              | mixed, strings                               |  100 | 1.71 ms | 3.75 ms |    17.1 µs |
| resolve(), second container, classes seen before             | mixed, strings                               |  100 | 97.4 µs |  101 µs |     974 ns |
| resolve(), cold                                              | mixed, strings                               | 1000 | 15.1 ms | 19.2 ms |    15.1 µs |
| resolve(), second container, classes seen before             | mixed, strings                               | 1000 | 1.21 ms | 2.42 ms |    1.21 µs |
| connect() + disconnect()                                     | wide: N independent clients                  |   10 |  259 µs |  330 µs |    25.9 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  |   10 | 2.21 µs | 4.11 µs |     221 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  |   10 |  257 µs |  327 µs |    25.7 µs |
| connect() + disconnect()                                     | wide: N independent clients                  |  100 | 1.55 ms | 1.70 ms |    15.5 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  |  100 | 15.1 µs | 16.3 µs |     151 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  |  100 | 1.53 ms | 1.68 ms |    15.3 µs |
| connect() + disconnect()                                     | wide: N independent clients                  | 1000 | 13.7 ms | 16.0 ms |    13.7 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  | 1000 |  168 µs |  210 µs |     168 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  | 1000 | 13.6 ms | 15.8 ms |    13.6 µs |
| connect() + disconnect()                                     | deep: a chain of N                           |   10 |  375 µs |  387 µs |    37.5 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           |   10 | 2.08 µs | 2.25 µs |     208 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           |   10 |  373 µs |  384 µs |    37.3 µs |
| connect() + disconnect()                                     | deep: a chain of N                           |  100 | 3.33 ms | 5.64 ms |    33.3 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           |  100 | 16.3 µs | 31.7 µs |     163 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           |  100 | 3.31 ms | 5.61 ms |    33.1 µs |
| connect() + disconnect()                                     | deep: a chain of N                           | 1000 | 29.6 ms | 31.8 ms |    29.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           | 1000 |  154 µs |  225 µs |     154 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           | 1000 | 29.4 ms | 31.6 ms |    29.4 µs |
| connect() + disconnect(), wall time                          | application: 8 clients, connect() of 1–60 ms |    8 | 87.2 ms | 87.5 ms |            |
| connect() + disconnect(), ideal: the critical path           | application: 8 clients, connect() of 1–60 ms |    8 | 87.0 ms | 87.2 ms |            |
| connect() + disconnect(), above the critical path            | application: 8 clients, connect() of 1–60 ms |    8 |  178 µs |  850 µs |            |
| inject(), clients resolved                                   | 2 clients                                    |      | 9.15 µs | 9.57 µs |            |
| call of the injected function                                | 2 clients                                    |      |  129 ns |  130 ns |            |
| call of the plain function                                   | 2 clients                                    |      | 42.0 ns | 47.7 ns |            |
| resolve(), cold                                              | N consumers of a Client                      |   10 | 60.6 µs | 72.1 µs |    6.06 µs |
| resolve(), cold                                              | N consumers of a Client                      |  100 |  478 µs |  538 µs |    4.78 µs |
| resolve(), cold                                              | N consumers of a Client                      | 1000 | 5.26 ms | 6.04 ms |    5.26 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          |   10 | 63.3 µs | 76.2 µs |    6.33 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          |  100 |  554 µs |  857 µs |    5.54 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          | 1000 | 5.46 ms | 6.32 ms |    5.46 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       |   10 |  813 µs | 1.02 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       |   10 | 14.9 µs | 19.1 µs |    1.49 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       |  100 |  802 µs | 1.10 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       |  100 |  109 µs |  113 µs |    1.09 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       | 1000 | 1.95 ms | 2.33 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       | 1000 | 1.09 ms | 1.28 ms |    1.09 µs |
| one request                                                  | a client through nuke-di                     |      |  106 µs |  110 µs |            |
| one request                                                  | a plain FastAPI Depends()                    |      |  106 µs |  111 µs |            |
| one request                                                  | no dependencies                              |      |  102 µs |  122 µs |            |
| import, fresh interpreter                                    | nuke_di                                      |      | 39.0 ms | 86.5 ms |            |
| import, fresh interpreter                                    | nuke_di.fastapi                              |      |  188 ms |  265 ms |            |
| import, fresh interpreter                                    | fastapi                                      |      |  182 ms |  235 ms |            |
| resolve(), tracemalloc peak                                  | mixed                                        | 1000 |  505 kB |  582 kB |      505 B |

### Python 3.13.14

nuke-di 1.12.0 · CPython 3.13.14 · macOS-26.6.2-arm64-arm-64bit-Mach-O · commit 59883af · N = 10, 100, 1000 · 20 repeats

| Scenario                                                     | Shape                                        |    N |  Median |     p95 | Per client |
|--------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                              | wide                                         |   10 | 44.0 µs | 55.7 µs |    4.40 µs |
| resolve(), second container, classes seen before             | wide                                         |   10 | 8.65 µs | 64.1 µs |     865 ns |
| resolve(), warm                                              | wide                                         |   10 |  207 ns |  507 ns |            |
| resolve(), cold                                              | wide                                         |  100 |  386 µs | 1.35 ms |    3.86 µs |
| resolve(), second container, classes seen before             | wide                                         |  100 |  104 µs |  165 µs |    1.04 µs |
| resolve(), warm                                              | wide                                         |  100 | 90.7 ns |  108 ns |            |
| resolve(), cold                                              | wide                                         | 1000 | 4.19 ms | 5.40 ms |    4.19 µs |
| resolve(), second container, classes seen before             | wide                                         | 1000 | 1.20 ms | 1.52 ms |    1.20 µs |
| resolve(), warm                                              | wide                                         | 1000 | 90.5 ns | 93.6 ns |            |
| resolve(), cold                                              | deep                                         |   10 | 40.6 µs | 82.6 µs |    4.06 µs |
| resolve(), second container, classes seen before             | deep                                         |   10 | 8.92 µs | 9.87 µs |     892 ns |
| resolve(), warm                                              | deep                                         |   10 | 88.0 ns | 95.5 ns |            |
| resolve(), cold                                              | deep                                         |  100 |  364 µs |  502 µs |    3.64 µs |
| resolve(), second container, classes seen before             | deep                                         |  100 | 94.4 µs |  112 µs |     944 ns |
| resolve(), warm                                              | deep                                         |  100 | 93.7 ns |  103 ns |            |
| resolve(), cold                                              | deep                                         | 1000 | 4.44 ms | 5.17 ms |    4.44 µs |
| resolve(), second container, classes seen before             | deep                                         | 1000 | 1.00 ms | 1.71 ms |    1.00 µs |
| resolve(), warm                                              | deep                                         | 1000 | 95.5 ns |  101 ns |            |
| resolve(), cold                                              | mixed                                        |   10 | 54.7 µs | 88.2 µs |    5.47 µs |
| resolve(), second container, classes seen before             | mixed                                        |   10 | 10.0 µs | 12.3 µs |    1.00 µs |
| resolve(), warm                                              | mixed                                        |   10 | 86.4 ns | 87.4 ns |            |
| resolve(), cold                                              | mixed                                        |  100 |  503 µs |  711 µs |    5.03 µs |
| resolve(), second container, classes seen before             | mixed                                        |  100 | 96.8 µs |  113 µs |     968 ns |
| resolve(), warm                                              | mixed                                        |  100 | 96.2 ns |  101 ns |            |
| resolve(), cold                                              | mixed                                        | 1000 | 4.87 ms | 5.70 ms |    4.87 µs |
| resolve(), second container, classes seen before             | mixed                                        | 1000 | 1.15 ms | 1.44 ms |    1.15 µs |
| resolve(), warm                                              | mixed                                        | 1000 | 94.8 ns |  121 ns |            |
| resolve(), cold                                              | wide, strings                                |   10 |  119 µs |  150 µs |    11.9 µs |
| resolve(), second container, classes seen before             | wide, strings                                |   10 | 8.15 µs | 11.6 µs |     815 ns |
| resolve(), cold                                              | wide, strings                                |  100 | 1.24 ms | 4.48 ms |    12.4 µs |
| resolve(), second container, classes seen before             | wide, strings                                |  100 | 87.0 µs |  101 µs |     870 ns |
| resolve(), cold                                              | wide, strings                                | 1000 | 12.5 ms | 13.3 ms |    12.5 µs |
| resolve(), second container, classes seen before             | wide, strings                                | 1000 | 1.18 ms | 1.55 ms |    1.18 µs |
| resolve(), cold                                              | deep, strings                                |   10 |  242 µs |  810 µs |    24.2 µs |
| resolve(), second container, classes seen before             | deep, strings                                |   10 | 8.88 µs | 9.84 µs |     888 ns |
| resolve(), cold                                              | deep, strings                                |  100 | 1.59 ms | 3.60 ms |    15.9 µs |
| resolve(), second container, classes seen before             | deep, strings                                |  100 |  130 µs |  192 µs |    1.30 µs |
| resolve(), cold                                              | deep, strings                                | 1000 | 15.1 ms | 30.7 ms |    15.1 µs |
| resolve(), second container, classes seen before             | deep, strings                                | 1000 |  907 µs | 1.48 ms |     907 ns |
| resolve(), cold                                              | mixed, strings                               |   10 |  223 µs |  571 µs |    22.3 µs |
| resolve(), second container, classes seen before             | mixed, strings                               |   10 | 9.73 µs | 10.6 µs |     973 ns |
| resolve(), cold                                              | mixed, strings                               |  100 | 2.12 ms | 3.91 ms |    21.2 µs |
| resolve(), second container, classes seen before             | mixed, strings                               |  100 | 94.2 µs | 97.7 µs |     942 ns |
| resolve(), cold                                              | mixed, strings                               | 1000 | 20.0 ms | 26.9 ms |    20.0 µs |
| resolve(), second container, classes seen before             | mixed, strings                               | 1000 | 2.01 ms | 4.54 ms |    2.01 µs |
| connect() + disconnect()                                     | wide: N independent clients                  |   10 |  566 µs | 1.24 ms |    56.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  |   10 | 3.10 µs | 4.79 µs |     310 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  |   10 |  561 µs | 1.24 ms |    56.1 µs |
| connect() + disconnect()                                     | wide: N independent clients                  |  100 | 1.46 ms | 1.94 ms |    14.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  |  100 | 14.9 µs | 22.5 µs |     149 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  |  100 | 1.44 ms | 1.92 ms |    14.4 µs |
| connect() + disconnect()                                     | wide: N independent clients                  | 1000 | 13.5 ms | 15.7 ms |    13.5 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  | 1000 |  170 µs |  273 µs |     170 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  | 1000 | 13.3 ms | 15.5 ms |    13.3 µs |
| connect() + disconnect()                                     | deep: a chain of N                           |   10 |  370 µs |  421 µs |    37.0 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           |   10 | 2.00 µs | 2.46 µs |     200 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           |   10 |  368 µs |  419 µs |    36.8 µs |
| connect() + disconnect()                                     | deep: a chain of N                           |  100 | 3.01 ms | 5.44 ms |    30.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           |  100 | 14.9 µs | 25.6 µs |     149 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           |  100 | 3.00 ms | 5.29 ms |    30.0 µs |
| connect() + disconnect()                                     | deep: a chain of N                           | 1000 | 28.8 ms | 37.7 ms |    28.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           | 1000 |  158 µs |  240 µs |     158 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           | 1000 | 28.6 ms | 37.5 ms |    28.6 µs |
| connect() + disconnect(), wall time                          | application: 8 clients, connect() of 1–60 ms |    8 | 87.3 ms | 89.2 ms |            |
| connect() + disconnect(), ideal: the critical path           | application: 8 clients, connect() of 1–60 ms |    8 | 87.0 ms | 87.2 ms |            |
| connect() + disconnect(), above the critical path            | application: 8 clients, connect() of 1–60 ms |    8 |  270 µs | 2.82 ms |            |
| inject(), clients resolved                                   | 2 clients                                    |      | 9.76 µs | 11.0 µs |            |
| call of the injected function                                | 2 clients                                    |      |  108 ns |  114 ns |            |
| call of the plain function                                   | 2 clients                                    |      | 37.8 ns | 39.2 ns |            |
| resolve(), cold                                              | N consumers of a Client                      |   10 | 64.2 µs | 75.1 µs |    6.42 µs |
| resolve(), cold                                              | N consumers of a Client                      |  100 |  538 µs |  606 µs |    5.38 µs |
| resolve(), cold                                              | N consumers of a Client                      | 1000 | 5.63 ms | 6.40 ms |    5.63 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          |   10 | 69.7 µs | 90.5 µs |    6.97 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          |  100 |  670 µs |  799 µs |    6.70 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          | 1000 | 6.57 ms | 8.69 ms |    6.57 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       |   10 |  885 µs | 1.23 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       |   10 | 14.0 µs | 20.6 µs |    1.40 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       |  100 | 1.01 ms | 1.17 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       |  100 |  108 µs |  148 µs |    1.08 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       | 1000 | 2.40 ms | 2.81 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       | 1000 | 1.14 ms | 1.67 ms |    1.14 µs |
| one request                                                  | a client through nuke-di                     |      |  109 µs |  135 µs |            |
| one request                                                  | a plain FastAPI Depends()                    |      |  109 µs |  114 µs |            |
| one request                                                  | no dependencies                              |      |  105 µs |  112 µs |            |
| import, fresh interpreter                                    | nuke_di                                      |      | 38.1 ms | 41.2 ms |            |
| import, fresh interpreter                                    | nuke_di.fastapi                              |      |  187 ms |  217 ms |            |
| import, fresh interpreter                                    | fastapi                                      |      |  167 ms |  181 ms |            |
| resolve(), tracemalloc peak                                  | mixed                                        | 1000 |  521 kB |  598 kB |      521 B |

### Python 3.14.6

nuke-di 1.12.0 · CPython 3.14.6 · macOS-26.6.2-arm64-arm-64bit-Mach-O · commit 59883af · N = 10, 100, 1000 · 20 repeats

| Scenario                                                     | Shape                                        |    N |  Median |     p95 | Per client |
|--------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                              | wide                                         |   10 | 45.1 µs | 53.5 µs |    4.51 µs |
| resolve(), second container, classes seen before             | wide                                         |   10 | 7.48 µs | 9.24 µs |     748 ns |
| resolve(), warm                                              | wide                                         |   10 | 84.6 ns | 85.3 ns |            |
| resolve(), cold                                              | wide                                         |  100 |  402 µs |  478 µs |    4.02 µs |
| resolve(), second container, classes seen before             | wide                                         |  100 | 84.9 µs | 96.2 µs |     849 ns |
| resolve(), warm                                              | wide                                         |  100 | 87.6 ns |  109 ns |            |
| resolve(), cold                                              | wide                                         | 1000 | 4.35 ms | 5.26 ms |    4.35 µs |
| resolve(), second container, classes seen before             | wide                                         | 1000 | 1.04 ms | 1.54 ms |    1.04 µs |
| resolve(), warm                                              | wide                                         | 1000 | 85.1 ns | 88.1 ns |            |
| resolve(), cold                                              | deep                                         |   10 | 53.3 µs | 67.7 µs |    5.33 µs |
| resolve(), second container, classes seen before             | deep                                         |   10 | 10.1 µs | 17.7 µs |    1.01 µs |
| resolve(), warm                                              | deep                                         |   10 | 90.9 ns |  101 ns |            |
| resolve(), cold                                              | deep                                         |  100 |  456 µs |  644 µs |    4.56 µs |
| resolve(), second container, classes seen before             | deep                                         |  100 | 86.0 µs | 91.4 µs |     860 ns |
| resolve(), warm                                              | deep                                         |  100 | 82.8 ns | 83.7 ns |            |
| resolve(), cold                                              | deep                                         | 1000 | 4.68 ms | 5.57 ms |    4.68 µs |
| resolve(), second container, classes seen before             | deep                                         | 1000 |  969 µs | 1.20 ms |     969 ns |
| resolve(), warm                                              | deep                                         | 1000 | 92.4 ns |  100 ns |            |
| resolve(), cold                                              | mixed                                        |   10 | 62.6 µs | 78.3 µs |    6.26 µs |
| resolve(), second container, classes seen before             | mixed                                        |   10 | 10.5 µs | 21.0 µs |    1.05 µs |
| resolve(), warm                                              | mixed                                        |   10 | 85.7 ns | 86.3 ns |            |
| resolve(), cold                                              | mixed                                        |  100 |  544 µs |  688 µs |    5.44 µs |
| resolve(), second container, classes seen before             | mixed                                        |  100 | 92.3 µs |  116 µs |     923 ns |
| resolve(), warm                                              | mixed                                        |  100 | 83.5 ns | 96.2 ns |            |
| resolve(), cold                                              | mixed                                        | 1000 | 5.39 ms | 6.03 ms |    5.39 µs |
| resolve(), second container, classes seen before             | mixed                                        | 1000 | 1.10 ms | 1.91 ms |    1.10 µs |
| resolve(), warm                                              | mixed                                        | 1000 | 84.9 ns | 85.8 ns |            |
| resolve(), cold                                              | wide, strings                                |   10 |  113 µs |  197 µs |    11.3 µs |
| resolve(), second container, classes seen before             | wide, strings                                |   10 | 7.73 µs | 8.38 µs |     773 ns |
| resolve(), cold                                              | wide, strings                                |  100 | 1.17 ms | 1.93 ms |    11.7 µs |
| resolve(), second container, classes seen before             | wide, strings                                |  100 | 86.4 µs |  105 µs |     864 ns |
| resolve(), cold                                              | wide, strings                                | 1000 | 21.7 ms | 31.7 ms |    21.7 µs |
| resolve(), second container, classes seen before             | wide, strings                                | 1000 | 1.09 ms | 1.39 ms |    1.09 µs |
| resolve(), cold                                              | deep, strings                                |   10 |  114 µs |  198 µs |    11.4 µs |
| resolve(), second container, classes seen before             | deep, strings                                |   10 | 8.56 µs | 9.42 µs |     856 ns |
| resolve(), cold                                              | deep, strings                                |  100 | 1.18 ms | 2.58 ms |    11.8 µs |
| resolve(), second container, classes seen before             | deep, strings                                |  100 | 85.8 µs | 88.3 µs |     858 ns |
| resolve(), cold                                              | deep, strings                                | 1000 | 22.5 ms | 26.9 ms |    22.5 µs |
| resolve(), second container, classes seen before             | deep, strings                                | 1000 |  910 µs | 1.20 ms |     910 ns |
| resolve(), cold                                              | mixed, strings                               |   10 |  247 µs |  507 µs |    24.7 µs |
| resolve(), second container, classes seen before             | mixed, strings                               |   10 | 9.37 µs | 10.7 µs |     937 ns |
| resolve(), cold                                              | mixed, strings                               |  100 | 2.12 ms | 2.63 ms |    21.2 µs |
| resolve(), second container, classes seen before             | mixed, strings                               |  100 | 98.0 µs |  120 µs |     980 ns |
| resolve(), cold                                              | mixed, strings                               | 1000 | 32.9 ms | 40.5 ms |    32.9 µs |
| resolve(), second container, classes seen before             | mixed, strings                               | 1000 | 1.03 ms | 1.23 ms |    1.03 µs |
| connect() + disconnect()                                     | wide: N independent clients                  |   10 |  232 µs |  264 µs |    23.2 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  |   10 | 2.31 µs | 2.99 µs |     231 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  |   10 |  229 µs |  261 µs |    22.9 µs |
| connect() + disconnect()                                     | wide: N independent clients                  |  100 | 1.77 ms | 4.31 ms |    17.7 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  |  100 | 24.9 µs | 64.4 µs |     249 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  |  100 | 1.75 ms | 4.24 ms |    17.5 µs |
| connect() + disconnect()                                     | wide: N independent clients                  | 1000 | 12.8 ms | 13.9 ms |    12.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N independent clients                  | 1000 |  184 µs |  241 µs |     184 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N independent clients                  | 1000 | 12.6 ms | 13.7 ms |    12.6 µs |
| connect() + disconnect()                                     | deep: a chain of N                           |   10 |  371 µs |  459 µs |    37.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           |   10 | 2.33 µs | 3.10 µs |     233 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           |   10 |  369 µs |  456 µs |    36.9 µs |
| connect() + disconnect()                                     | deep: a chain of N                           |  100 | 2.86 ms | 3.21 ms |    28.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           |  100 | 15.7 µs | 21.0 µs |     157 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           |  100 | 2.85 ms | 3.20 ms |    28.5 µs |
| connect() + disconnect()                                     | deep: a chain of N                           | 1000 | 28.4 ms | 31.8 ms |    28.4 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: a chain of N                           | 1000 |  171 µs |  317 µs |     171 ns |
| connect() + disconnect(), overhead above the ideal           | deep: a chain of N                           | 1000 | 28.2 ms | 31.5 ms |    28.2 µs |
| connect() + disconnect(), wall time                          | application: 8 clients, connect() of 1–60 ms |    8 | 87.2 ms | 88.3 ms |            |
| connect() + disconnect(), ideal: the critical path           | application: 8 clients, connect() of 1–60 ms |    8 | 87.0 ms | 89.1 ms |            |
| connect() + disconnect(), above the critical path            | application: 8 clients, connect() of 1–60 ms |    8 | 87.0 µs | 1.14 ms |            |
| inject(), clients resolved                                   | 2 clients                                    |      | 9.41 µs | 9.54 µs |            |
| call of the injected function                                | 2 clients                                    |      |  118 ns |  126 ns |            |
| call of the plain function                                   | 2 clients                                    |      | 37.8 ns | 38.5 ns |            |
| resolve(), cold                                              | N consumers of a Client                      |   10 | 66.3 µs | 81.3 µs |    6.63 µs |
| resolve(), cold                                              | N consumers of a Client                      |  100 |  543 µs |  665 µs |    5.43 µs |
| resolve(), cold                                              | N consumers of a Client                      | 1000 | 5.66 ms | 6.48 ms |    5.66 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          |   10 | 71.9 µs | 82.4 µs |    7.19 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          |  100 |  627 µs |  790 µs |    6.27 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient          | 1000 | 6.79 ms | 7.73 ms |    6.79 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       |   10 |  839 µs |  936 µs |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       |   10 | 14.4 µs | 21.4 µs |    1.44 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       |  100 | 1.00 ms | 1.21 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       |  100 |  106 µs |  124 µs |    1.06 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced                       | 1000 | 2.06 ms | 2.45 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced                       | 1000 | 1.09 ms | 1.31 ms |    1.09 µs |
| one request                                                  | a client through nuke-di                     |      |  114 µs |  132 µs |            |
| one request                                                  | a plain FastAPI Depends()                    |      |  114 µs |  116 µs |            |
| one request                                                  | no dependencies                              |      |  107 µs |  111 µs |            |
| import, fresh interpreter                                    | nuke_di                                      |      | 35.5 ms | 50.2 ms |            |
| import, fresh interpreter                                    | nuke_di.fastapi                              |      |  172 ms |  181 ms |            |
| import, fresh interpreter                                    | fastapi                                      |      |  149 ms |  157 ms |            |
| resolve(), tracemalloc peak                                  | mixed                                        | 1000 |  521 kB |  597 kB |      521 B |
