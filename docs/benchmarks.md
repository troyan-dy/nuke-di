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
| `resolve(), second container, classes seen before` | the same six trees | `resolve()` of the root on a fresh `Dependencies()` after the classes were resolved once in the process: what every test of a session pays after the first, and the row a per-class cache ([#29](https://github.com/troyan-dy/nuke-di/issues/29)) would move |
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
  ([#35](https://github.com/troyan-dy/nuke-di/issues/35)). The baseline below predates it: the recursion of
  1.9.0 stopped a chain at about 400 clients on Python 3.11 and 800 on 3.12 and later, and the runner
  raised the limit for its deep trees. `benchmarks/compare.py` still raises it, for the libraries that recurse.
- The `connect()` figures include the logging calls of the container (the `nuke_di` logger with no
  handler) and a `ClientTiming` per client, which is what a real startup pays too.
- The application figures are wall time, and `asyncio.sleep()` overshoots by up to a millisecond per
  sleep in a chain, which the container and the critical path both pay: compare the two with each other,
  not either with the nominal 105 and 84 ms.

## Findings

The baseline was taken on an Apple M2 Pro, macOS 26.6.2 (arm64), `nuke-di` 1.9.0 at commit `8a414d6`,
Python 3.11.7, 3.12.5, 3.13.14 and 3.14.6, each in a fresh `uv` environment from `uv.lock`, with
`N = 10, 100, 1000` and 20 repeats. It is the state before the per-class cache and the `__init__` reader
of 1.9.1 ([#29](https://github.com/troyan-dy/nuke-di/issues/29),
[#36](https://github.com/troyan-dy/nuke-di/issues/36)): the tables and the findings below stand as the
"before" figures until the baseline is retaken once every performance change is in.

- **`resolve()` costs 7–14 µs per client** with real type hints on every version and grows linearly:
  100 clients in about 1 ms, 1000 in 8–14 ms. The chain is the most expensive shape per client (one
  recursion level per client), the wide tree the cheapest. A warm `resolve()`, the singleton cache hit,
  is 110–130 ns. The "200 clients in about 2 ms" of
  [#16](https://github.com/troyan-dy/nuke-di/issues/16) holds.
- **String annotations cost 1.3–3.5 times the real-type figure**: 11–38 µs per client. The ratio is
  1.3–1.8 on 3.11, 1.6–2.4 on 3.12, 1.7–2.7 on 3.13 and 2.0–3.5 on 3.14, and the mixed tree pays the
  most, with two or three hints per class against one: `get_type_hints()` compiles and evaluates every
  string on every cold `resolve()`, and the `annotationlib` of 3.14 does more per string. Most code bases
  have `from __future__ import annotations`, so this is the figure they pay, not the one above.
- **A second container costs the same as the first**: `resolve()` of classes seen before is 10–20% below
  the cold figure at `N = 10` and within 10% of it at 100 and 1000, but for one tree (3.12, `wide,
  strings`, 1000: 22% below), with both kinds of hints; nothing is kept per class, so every container
  reads the signatures again. This is the row a per-class cache
  ([#29](https://github.com/troyan-dy/nuke-di/issues/29)) would move. The cold figure is above it most
  likely for the first attribute lookups on fresh classes, which fill the type caches of the interpreter.
- **`connect()` and `disconnect()` cost 12–23 µs per client in a layer and 0.1–0.24 ms per layer**,
  all of it scheduling: the clients' own coroutines take 0.2 µs each. A layer is cheapest on 3.14 and
  most expensive on 3.11, where `asyncio.wait_for()` created a task per call, which doubled the cost of
  a chain; [#30](https://github.com/troyan-dy/nuke-di/issues/30) replaced it with `asyncio.timeout()`
  after these figures were taken, which brought the chain of 1000 clients on 3.11 from 184 ms to 104 ms.
  Not a hot spot: a real `connect()` takes milliseconds, a hundred to a thousand times more than its
  scheduling.
- **The application connects and disconnects in 114–116 ms of wall time against a critical path of
  89–90 ms**: 25–26 ms, 22%, is lost at the layer barriers, where `Repository` and `Users` wait for
  `Kafka` although `Postgres` connected 30 ms earlier. The figure is the same on every version, because
  it is the clients' sleeps and not the scheduling; it is what a schedule by dependency
  ([#28](https://github.com/troyan-dy/nuke-di/issues/28)) would recover.
- **`inject()` takes 8–10 µs** to bind a function with two clients. The `partial` it returns adds
  45–90 ns to a call that takes 36–41 ns without it.
- **A `NotSingletonClient` costs 5–8 µs more per consumer** than the shared instance of a `Client`.
- **The test cycle is `resolve()` plus the mock.** The autospec of `mock()` costs 1.3–1.6 ms whatever
  the tree; `override()` with an instance costs the same as a plain `resolve()`.
- **A FastAPI handler that takes a client through `nuke-di` costs the same as one with a plain
  `Depends()`**: 104–118 µs per request for both, within the noise, and 5–9 µs above a handler
  without dependencies. The ASGI stack is the cost, not the injection.
- **`import nuke_di` takes 27–37 ms**, 19 ms of it `asyncio` and 5 ms `logging`, both imported through
  `nuke_di.clients`; `nuke_di.core` itself is 2 ms. `nuke_di.fastapi` adds 13–28 ms on top of
  `fastapi`'s 137–167 ms. This is the figure the lazy-import work in
  [#14](https://github.com/troyan-dy/nuke-di/issues/14) moves.
- **A resolved client takes about 600 bytes**: 580–600 kB for the 1000-client mixed tree, up from 400
  in 1.8.0, which is the record of its dependencies that `graph()` reads, added in 1.9.0.
- Between versions, `resolve()` with real type hints is within 20% (3.11 the fastest) and with strings
  3.14 is the slowest by far, `connect()` is fastest on 3.14, and the request figures of 3.14 are a few
  percent above the others.

## Comparison with other libraries

`benchmarks/compare.py` runs the same trees through [dishka](https://github.com/reagento/dishka),
[wireup](https://github.com/maldoinc/wireup),
[dependency-injector](https://github.com/ets-labs/python-dependency-injector) and
[injector](https://github.com/python-injector/injector), the libraries a project choosing `nuke-di`
would otherwise consider. `make bench-compare` writes the JSON into `docs/benchmarks/`. Every library
gets the same classes, with the dependencies in the type hints of `__init__`, and does the same work:

| Scenario | The figure |
|----------|------------|
| `cold: container, registration, root` | Create a container, register the `N` classes and get the root, which constructs every client of the tree: what an application pays once at startup. For dishka and wireup it includes the validation of the graph their container does on creation; for dependency-injector, creating one `Singleton` provider per class on a `DynamicContainer`; for injector, an `Injector` with a binding per class. The `strings` trees go through the same code: every library reads the string annotations of a class in a registered module, dishka, wireup and injector through `get_type_hints()` as `nuke-di` does, dependency-injector not at all, its providers are wired by the names of the `__init__` arguments, so strings cost it nothing |
| `warm: the root again` | Get the root again from that container: the singleton, independent of `N` |
| `one request, a client in the handler` | One FastAPI request to a handler that takes one client through the library's integration: `nuke_di.fastapi`, `DishkaRoute` with `FromDishka[...]`, `wireup.integration.fastapi` with `Injected[...]`, `@inject` with `Depends(Provide[...])` for dependency-injector, each as its documentation shows. injector has no integration of its own |

What is done once outside the timing, as a user does it at import: wireup's `@injectable` and
injector's `@inject` on the classes; the latter evaluates the type hints of `__init__` right there,
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
| Cold start: a container and a tree of 100 clients        | **874 µs**     | 13.7 ms (15.6×) | 22.7 ms (26.0×) | 975 µs (1.1×)       | 1.43 ms (1.6×)  |
| Cold start: the same 100 clients with string annotations | 1.72 ms (1.8×) | 14.2 ms (14.7×) | 22.6 ms (23.4×) | **966 µs**          | 1.35 ms (1.4×)  |
| A cached root                                            | 116 ns (3.1×)  | 274 ns (7.4×)   | 95.6 ns (2.6×)  | **37.1 ns**         | 1.22 µs (32.9×) |
| A FastAPI request with a client                          | **104 µs**     | 107 µs (1.0×)   | 229 µs (2.2×)   | 232 µs (2.2×)       | —               |

The comparison was taken on the same machine, Python 3.11.7, with `N = 10, 100, 1000` and 20 repeats,
on dishka 1.10.1, wireup 2.12.1, dependency-injector 4.49.1 and injector 0.24.0.

- **A cold tree costs 7–13 µs per client in `nuke-di`**, the same as dependency-injector (8–12 µs) and
  injector (11–17 µs), which, like `nuke-di`, read the signatures and build the tree on demand. dishka
  (90–190 µs per client) and wireup (190–610 µs) validate the whole graph when the container is created:
  10–50 times more, 0.1–0.6 s for 1000 clients. That is the price of their startup checks, paid once.
- **With string annotations `nuke-di` costs 10–19 µs per client, 1.4–2.1 times its real-type figure**,
  which turns the cold-start row around at 100 clients: dependency-injector (8–12 µs, wired by name, no
  annotation read) and injector (11–16 µs, the hints evaluated at import) are 1.8 and 1.3 times ahead.
  dishka and wireup read the strings too and pay 0–15% more for them, a few percent of their
  validation. The row is the gain a per-class cache ([#29](https://github.com/troyan-dy/nuke-di/issues/29))
  is measured against.
- **A cached root costs 40–275 ns**: 37 ns in dependency-injector (Cython), 92–99 ns in wireup, 113–116 ns
  in `nuke-di`, 268–274 ns in dishka, and 1.2 µs in injector, which resolves the binding on every `get()`.
- **A FastAPI request through `nuke-di` or dishka costs 104–107 µs**, the same as a plain `Depends()`
  (104–118 µs in the baseline above). Through wireup it costs 229 µs and through dependency-injector
  232 µs, twice that: their integrations do more per request, as their documentation wires them; what
  exactly is not investigated here.
- A chain of 1000 clients exceeds the default recursion limit in dishka, wireup and injector, in
  dependency-injector on 3.11 (not on 3.14), and in the `nuke-di` 1.9.0 of this comparison, which recursed
  until [#35](https://github.com/troyan-dy/nuke-di/issues/35); the runner raises the limit, which is enough
  for every library but injector.

### Python 3.11.7, nuke-di 1.9.0 · dishka 1.10.1 · wireup 2.12.1 · dependency-injector 4.49.1 · injector 0.24.0

nuke-di 1.9.0 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 8a414d6 · N = 10, 100, 1000 · 20 repeats

| Library             | Scenario                             | Shape          |    N |         Median |     p95 | Per client |
|---------------------|--------------------------------------|----------------|-----:|---------------:|--------:|-----------:|
| nuke-di             | cold: container, registration, root  | wide           |   10 |        72.6 µs | 93.3 µs |    7.26 µs |
| dishka              | cold: container, registration, root  | wide           |   10 |        1.43 ms | 1.66 ms |     143 µs |
| wireup              | cold: container, registration, root  | wide           |   10 |        2.25 ms | 2.70 ms |     225 µs |
| dependency-injector | cold: container, registration, root  | wide           |   10 |        81.6 µs | 90.5 µs |    8.16 µs |
| injector            | cold: container, registration, root  | wide           |   10 |         116 µs |  207 µs |    11.6 µs |
| nuke-di             | cold: container, registration, root  | wide           |  100 |         766 µs | 1.57 ms |    7.66 µs |
| dishka              | cold: container, registration, root  | wide           |  100 |        9.49 ms | 10.8 ms |    94.9 µs |
| wireup              | cold: container, registration, root  | wide           |  100 |        19.3 ms | 20.1 ms |     193 µs |
| dependency-injector | cold: container, registration, root  | wide           |  100 |         803 µs |  821 µs |    8.03 µs |
| injector            | cold: container, registration, root  | wide           |  100 |        1.08 ms | 1.15 ms |    10.8 µs |
| nuke-di             | cold: container, registration, root  | wide           | 1000 |        7.48 ms | 7.81 ms |    7.48 µs |
| dishka              | cold: container, registration, root  | wide           | 1000 |        92.2 ms | 98.7 ms |    92.2 µs |
| wireup              | cold: container, registration, root  | wide           | 1000 |         196 ms |  207 ms |     196 µs |
| dependency-injector | cold: container, registration, root  | wide           | 1000 |        12.0 ms | 13.5 ms |    12.0 µs |
| injector            | cold: container, registration, root  | wide           | 1000 |        11.3 ms | 12.6 ms |    11.3 µs |
| nuke-di             | cold: container, registration, root  | deep           |   10 |        72.7 µs | 83.4 µs |    7.27 µs |
| dishka              | cold: container, registration, root  | deep           |   10 |        1.70 ms | 1.97 ms |     170 µs |
| wireup              | cold: container, registration, root  | deep           |   10 |        2.29 ms | 2.65 ms |     229 µs |
| dependency-injector | cold: container, registration, root  | deep           |   10 |        89.2 µs |  111 µs |    8.92 µs |
| injector            | cold: container, registration, root  | deep           |   10 |         142 µs |  175 µs |    14.2 µs |
| nuke-di             | cold: container, registration, root  | deep           |  100 |         871 µs |  997 µs |    8.71 µs |
| dishka              | cold: container, registration, root  | deep           |  100 |        13.0 ms | 14.6 ms |     130 µs |
| wireup              | cold: container, registration, root  | deep           |  100 |        23.2 ms | 25.1 ms |     232 µs |
| dependency-injector | cold: container, registration, root  | deep           |  100 |         854 µs |  911 µs |    8.54 µs |
| injector            | cold: container, registration, root  | deep           |  100 |        1.38 ms | 1.43 ms |    13.8 µs |
| nuke-di             | cold: container, registration, root  | deep           | 1000 |        12.8 ms | 14.3 ms |    12.8 µs |
| dishka              | cold: container, registration, root  | deep           | 1000 |         138 ms |  148 ms |     138 µs |
| wireup              | cold: container, registration, root  | deep           | 1000 |         613 ms |  642 ms |     613 µs |
| dependency-injector | cold: container, registration, root  | deep           | 1000 |        9.34 ms | 11.0 ms |    9.34 µs |
| injector            | cold: container, registration, root  | deep           | 1000 | RecursionError |         |            |
| nuke-di             | cold: container, registration, root  | mixed          |   10 |        88.5 µs | 96.8 µs |    8.85 µs |
| dishka              | cold: container, registration, root  | mixed          |   10 |        1.89 ms | 2.15 ms |     189 µs |
| wireup              | cold: container, registration, root  | mixed          |   10 |        2.56 ms | 2.85 ms |     256 µs |
| dependency-injector | cold: container, registration, root  | mixed          |   10 |        97.3 µs |  101 µs |    9.73 µs |
| injector            | cold: container, registration, root  | mixed          |   10 |         145 µs |  162 µs |    14.5 µs |
| nuke-di             | cold: container, registration, root  | mixed          |  100 |         874 µs |  922 µs |    8.74 µs |
| dishka              | cold: container, registration, root  | mixed          |  100 |        13.7 ms | 17.9 ms |     137 µs |
| wireup              | cold: container, registration, root  | mixed          |  100 |        22.7 ms | 23.7 ms |     227 µs |
| dependency-injector | cold: container, registration, root  | mixed          |  100 |         975 µs | 1.07 ms |    9.75 µs |
| injector            | cold: container, registration, root  | mixed          |  100 |        1.43 ms | 1.51 ms |    14.3 µs |
| nuke-di             | cold: container, registration, root  | mixed          | 1000 |        9.08 ms | 9.73 ms |    9.08 µs |
| dishka              | cold: container, registration, root  | mixed          | 1000 |         124 ms |  127 ms |     124 µs |
| wireup              | cold: container, registration, root  | mixed          | 1000 |         239 ms |  257 ms |     239 µs |
| dependency-injector | cold: container, registration, root  | mixed          | 1000 |        9.26 ms | 10.2 ms |    9.26 µs |
| injector            | cold: container, registration, root  | mixed          | 1000 |        17.1 ms | 18.7 ms |    17.1 µs |
| nuke-di             | cold: container, registration, root  | wide, strings  |   10 |         101 µs |  109 µs |    10.1 µs |
| dishka              | cold: container, registration, root  | wide, strings  |   10 |        1.59 ms | 1.87 ms |     159 µs |
| wireup              | cold: container, registration, root  | wide, strings  |   10 |        2.35 ms | 2.98 ms |     235 µs |
| dependency-injector | cold: container, registration, root  | wide, strings  |   10 |        83.1 µs | 91.8 µs |    8.31 µs |
| injector            | cold: container, registration, root  | wide, strings  |   10 |         115 µs |  122 µs |    11.5 µs |
| nuke-di             | cold: container, registration, root  | wide, strings  |  100 |        1.13 ms | 1.92 ms |    11.3 µs |
| dishka              | cold: container, registration, root  | wide, strings  |  100 |        11.0 ms | 13.4 ms |     110 µs |
| wireup              | cold: container, registration, root  | wide, strings  |  100 |        21.2 ms | 22.4 ms |     212 µs |
| dependency-injector | cold: container, registration, root  | wide, strings  |  100 |         836 µs |  872 µs |    8.36 µs |
| injector            | cold: container, registration, root  | wide, strings  |  100 |        1.13 ms | 1.17 ms |    11.3 µs |
| nuke-di             | cold: container, registration, root  | wide, strings  | 1000 |        13.5 ms | 18.8 ms |    13.5 µs |
| dishka              | cold: container, registration, root  | wide, strings  | 1000 |         102 ms |  109 ms |     102 µs |
| wireup              | cold: container, registration, root  | wide, strings  | 1000 |         220 ms |  234 ms |     220 µs |
| dependency-injector | cold: container, registration, root  | wide, strings  | 1000 |        11.9 ms | 12.7 ms |    11.9 µs |
| injector            | cold: container, registration, root  | wide, strings  | 1000 |        11.3 ms | 12.8 ms |    11.3 µs |
| nuke-di             | cold: container, registration, root  | deep, strings  |   10 |         119 µs |  139 µs |    11.9 µs |
| dishka              | cold: container, registration, root  | deep, strings  |   10 |        1.73 ms | 2.17 ms |     173 µs |
| wireup              | cold: container, registration, root  | deep, strings  |   10 |        2.31 ms | 2.64 ms |     231 µs |
| dependency-injector | cold: container, registration, root  | deep, strings  |   10 |        94.3 µs |  108 µs |    9.43 µs |
| injector            | cold: container, registration, root  | deep, strings  |   10 |         142 µs |  172 µs |    14.2 µs |
| nuke-di             | cold: container, registration, root  | deep, strings  |  100 |        1.41 ms | 2.50 ms |    14.1 µs |
| dishka              | cold: container, registration, root  | deep, strings  |  100 |        13.1 ms | 14.3 ms |     131 µs |
| wireup              | cold: container, registration, root  | deep, strings  |  100 |        24.0 ms | 24.8 ms |     240 µs |
| dependency-injector | cold: container, registration, root  | deep, strings  |  100 |         865 µs | 1.03 ms |    8.65 µs |
| injector            | cold: container, registration, root  | deep, strings  |  100 |        1.28 ms | 1.35 ms |    12.8 µs |
| nuke-di             | cold: container, registration, root  | deep, strings  | 1000 |        18.9 ms | 21.7 ms |    18.9 µs |
| dishka              | cold: container, registration, root  | deep, strings  | 1000 |         148 ms |  167 ms |     148 µs |
| wireup              | cold: container, registration, root  | deep, strings  | 1000 |         633 ms |  659 ms |     633 µs |
| dependency-injector | cold: container, registration, root  | deep, strings  | 1000 |        9.30 ms | 10.8 ms |    9.30 µs |
| injector            | cold: container, registration, root  | deep, strings  | 1000 | RecursionError |         |            |
| nuke-di             | cold: container, registration, root  | mixed, strings |   10 |         188 µs |  365 µs |    18.8 µs |
| dishka              | cold: container, registration, root  | mixed, strings |   10 |        2.04 ms | 2.67 ms |     204 µs |
| wireup              | cold: container, registration, root  | mixed, strings |   10 |        2.69 ms | 3.01 ms |     269 µs |
| dependency-injector | cold: container, registration, root  | mixed, strings |   10 |         107 µs |  137 µs |    10.7 µs |
| injector            | cold: container, registration, root  | mixed, strings |   10 |         138 µs |  192 µs |    13.8 µs |
| nuke-di             | cold: container, registration, root  | mixed, strings |  100 |        1.72 ms | 2.37 ms |    17.2 µs |
| dishka              | cold: container, registration, root  | mixed, strings |  100 |        14.2 ms | 15.3 ms |     142 µs |
| wireup              | cold: container, registration, root  | mixed, strings |  100 |        22.6 ms | 23.4 ms |     226 µs |
| dependency-injector | cold: container, registration, root  | mixed, strings |  100 |         966 µs |  978 µs |    9.66 µs |
| injector            | cold: container, registration, root  | mixed, strings |  100 |        1.35 ms | 1.38 ms |    13.5 µs |
| nuke-di             | cold: container, registration, root  | mixed, strings | 1000 |        14.5 ms | 16.1 ms |    14.5 µs |
| dishka              | cold: container, registration, root  | mixed, strings | 1000 |         126 ms |  132 ms |     126 µs |
| wireup              | cold: container, registration, root  | mixed, strings | 1000 |         276 ms |  289 ms |     276 µs |
| dependency-injector | cold: container, registration, root  | mixed, strings | 1000 |        9.31 ms | 10.7 ms |    9.31 µs |
| injector            | cold: container, registration, root  | mixed, strings | 1000 |        15.6 ms | 17.6 ms |    15.6 µs |
| nuke-di             | warm: the root again                 | wide           |   10 |         115 ns |  125 ns |            |
| dishka              | warm: the root again                 | wide           |   10 |         276 ns |  295 ns |            |
| wireup              | warm: the root again                 | wide           |   10 |        96.6 ns |  104 ns |            |
| dependency-injector | warm: the root again                 | wide           |   10 |        38.1 ns | 38.4 ns |            |
| injector            | warm: the root again                 | wide           |   10 |        1.24 µs | 1.34 µs |            |
| nuke-di             | warm: the root again                 | wide           |  100 |         113 ns |  128 ns |            |
| dishka              | warm: the root again                 | wide           |  100 |         270 ns |  286 ns |            |
| wireup              | warm: the root again                 | wide           |  100 |        92.0 ns | 95.4 ns |            |
| dependency-injector | warm: the root again                 | wide           |  100 |        37.2 ns | 44.0 ns |            |
| injector            | warm: the root again                 | wide           |  100 |        1.23 µs | 1.29 µs |            |
| nuke-di             | warm: the root again                 | wide           | 1000 |         114 ns |  131 ns |            |
| dishka              | warm: the root again                 | wide           | 1000 |         289 ns |  303 ns |            |
| wireup              | warm: the root again                 | wide           | 1000 |        96.4 ns |  104 ns |            |
| dependency-injector | warm: the root again                 | wide           | 1000 |        40.3 ns | 47.3 ns |            |
| injector            | warm: the root again                 | wide           | 1000 |        1.25 µs | 1.29 µs |            |
| nuke-di             | warm: the root again                 | deep           |   10 |         119 ns |  125 ns |            |
| dishka              | warm: the root again                 | deep           |   10 |         288 ns |  303 ns |            |
| wireup              | warm: the root again                 | deep           |   10 |        99.5 ns |  112 ns |            |
| dependency-injector | warm: the root again                 | deep           |   10 |        40.5 ns | 49.2 ns |            |
| injector            | warm: the root again                 | deep           |   10 |        1.23 µs | 1.30 µs |            |
| nuke-di             | warm: the root again                 | deep           |  100 |         115 ns |  122 ns |            |
| dishka              | warm: the root again                 | deep           |  100 |         268 ns |  288 ns |            |
| wireup              | warm: the root again                 | deep           |  100 |        99.2 ns |  107 ns |            |
| dependency-injector | warm: the root again                 | deep           |  100 |        39.6 ns | 45.0 ns |            |
| injector            | warm: the root again                 | deep           |  100 |        1.23 µs | 1.32 µs |            |
| nuke-di             | warm: the root again                 | deep           | 1000 |         112 ns |  120 ns |            |
| dishka              | warm: the root again                 | deep           | 1000 |         263 ns |  267 ns |            |
| wireup              | warm: the root again                 | deep           | 1000 |         101 ns |  107 ns |            |
| dependency-injector | warm: the root again                 | deep           | 1000 |        39.3 ns | 46.0 ns |            |
| injector            | warm: the root again                 | deep           | 1000 | RecursionError |         |            |
| nuke-di             | warm: the root again                 | mixed          |   10 |         108 ns |  119 ns |            |
| dishka              | warm: the root again                 | mixed          |   10 |         268 ns |  278 ns |            |
| wireup              | warm: the root again                 | mixed          |   10 |        95.7 ns |  102 ns |            |
| dependency-injector | warm: the root again                 | mixed          |   10 |        37.4 ns | 43.7 ns |            |
| injector            | warm: the root again                 | mixed          |   10 |        1.25 µs | 1.31 µs |            |
| nuke-di             | warm: the root again                 | mixed          |  100 |         116 ns |  122 ns |            |
| dishka              | warm: the root again                 | mixed          |  100 |         274 ns |  296 ns |            |
| wireup              | warm: the root again                 | mixed          |  100 |        95.6 ns |  102 ns |            |
| dependency-injector | warm: the root again                 | mixed          |  100 |        37.1 ns | 44.1 ns |            |
| injector            | warm: the root again                 | mixed          |  100 |        1.22 µs | 1.25 µs |            |
| nuke-di             | warm: the root again                 | mixed          | 1000 |         112 ns |  123 ns |            |
| dishka              | warm: the root again                 | mixed          | 1000 |         272 ns |  306 ns |            |
| wireup              | warm: the root again                 | mixed          | 1000 |        95.3 ns | 96.8 ns |            |
| dependency-injector | warm: the root again                 | mixed          | 1000 |        38.7 ns | 43.2 ns |            |
| injector            | warm: the root again                 | mixed          | 1000 |        1.27 µs | 1.30 µs |            |
| nuke-di             | one request, a client in the handler | FastAPI        |      |         104 µs |  109 µs |            |
| dishka              | one request, a client in the handler | FastAPI        |      |         107 µs |  111 µs |            |
| wireup              | one request, a client in the handler | FastAPI        |      |         229 µs |  295 µs |            |
| dependency-injector | one request, a client in the handler | FastAPI        |      |         232 µs |  281 µs |            |

## Baseline

### Python 3.11.7

nuke-di 1.9.0 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 8a414d6 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |     p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 76.7 µs | 90.8 µs |    7.67 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 65.8 µs | 76.1 µs |    6.58 µs |
| resolve(), warm                                                       | wide                                         |   10 |  106 ns |  106 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  705 µs |  993 µs |    7.05 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  677 µs |  843 µs |    6.77 µs |
| resolve(), warm                                                       | wide                                         |  100 |  109 ns |  118 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 7.69 ms | 8.71 ms |    7.69 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 7.90 ms | 8.60 ms |    7.90 µs |
| resolve(), warm                                                       | wide                                         | 1000 |  107 ns |  114 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 85.3 µs |  110 µs |    8.53 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 70.9 µs | 88.6 µs |    7.09 µs |
| resolve(), warm                                                       | deep                                         |   10 |  115 ns |  120 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  970 µs | 1.17 ms |    9.70 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  873 µs | 1.28 ms |    8.73 µs |
| resolve(), warm                                                       | deep                                         |  100 |  102 ns |  103 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 13.8 ms | 14.9 ms |    13.8 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 13.0 ms | 14.1 ms |    13.0 µs |
| resolve(), warm                                                       | deep                                         | 1000 |  116 ns |  127 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 | 97.0 µs |  122 µs |    9.70 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 82.4 µs | 85.6 µs |    8.24 µs |
| resolve(), warm                                                       | mixed                                        |   10 |  109 ns |  125 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 |  932 µs | 1.08 ms |    9.32 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  828 µs |  904 µs |    8.28 µs |
| resolve(), warm                                                       | mixed                                        |  100 |  113 ns |  122 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 8.71 ms | 9.42 ms |    8.71 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 8.49 ms | 9.24 ms |    8.49 µs |
| resolve(), warm                                                       | mixed                                        | 1000 |  116 ns |  128 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 |  115 µs |  162 µs |    11.5 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 |  125 µs |  550 µs |    12.5 µs |
| resolve(), cold                                                       | wide, strings                                |  100 | 1.09 ms | 1.31 ms |    10.9 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 |  961 µs | 1.09 ms |    9.61 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 11.1 ms | 11.8 ms |    11.1 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 10.3 ms | 11.5 ms |    10.3 µs |
| resolve(), cold                                                       | deep, strings                                |   10 |  113 µs |  136 µs |    11.3 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 | 97.5 µs |  106 µs |    9.75 µs |
| resolve(), cold                                                       | deep, strings                                |  100 | 1.34 ms | 1.63 ms |    13.4 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 | 1.22 ms | 1.75 ms |    12.2 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 18.4 ms | 24.4 ms |    18.4 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 18.2 ms | 19.5 ms |    18.2 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  173 µs |  192 µs |    17.3 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 |  152 µs |  157 µs |    15.2 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 1.59 ms | 1.94 ms |    15.9 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 | 1.52 ms | 1.65 ms |    15.2 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 14.2 ms | 15.0 ms |    14.2 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 14.1 ms | 14.6 ms |    14.1 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  406 µs |  580 µs |    40.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 2.50 µs | 3.07 µs |     250 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  403 µs |  578 µs |    40.3 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 2.33 ms | 2.56 ms |    23.3 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 20.0 µs | 65.1 µs |     200 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 2.31 ms | 2.53 ms |    23.1 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 19.4 ms | 26.5 ms |    19.4 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  265 µs |  392 µs |     265 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 19.1 ms | 26.1 ms |    19.1 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 | 1.88 ms | 2.19 ms |     188 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.25 µs | 6.27 µs |     225 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 | 1.88 ms | 2.18 ms |     188 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 23.8 ms | 25.9 ms |     238 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 28.4 µs | 39.3 µs |     284 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 23.8 ms | 25.9 ms |     238 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 |  211 ms |  248 ms |     211 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  345 µs |  452 µs |     345 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 |  211 ms |  248 ms |     211 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  116 ms |  116 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 89.3 ms | 90.2 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 26.1 ms | 27.5 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 8.09 µs | 8.61 µs |            |
| call of the injected function                                         | 2 clients                                    |      | 82.2 ns | 83.8 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 36.2 ns | 38.3 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 |  116 µs |  128 µs |    11.6 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 |  948 µs | 1.03 ms |    9.48 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 9.59 ms | 10.7 ms |    9.59 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 |  158 µs |  166 µs |    15.8 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 | 1.49 ms | 1.66 ms |    14.9 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 15.6 ms | 17.5 ms |    15.6 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 | 1.42 ms | 1.67 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 83.2 µs | 87.8 µs |    8.32 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 | 2.13 ms | 2.45 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  839 µs |  972 µs |    8.39 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 10.4 ms | 11.0 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 8.91 ms | 10.2 ms |    8.91 µs |
| one request                                                           | a client through nuke-di                     |      |  104 µs |  109 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  107 µs |  125 µs |            |
| one request                                                           | no dependencies                              |      | 98.1 µs |  103 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 27.3 ms | 31.9 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  152 ms |  182 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  138 ms |  148 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  590 kB |  665 kB |      590 B |

### Python 3.12.5

nuke-di 1.9.0 · CPython 3.12.5 · macOS-26.6.2-arm64-arm-64bit · commit 8a414d6 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |   Median |     p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|---------:|--------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 |  85.9 µs |  102 µs |    8.59 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 |  71.8 µs | 73.4 µs |    7.18 µs |
| resolve(), warm                                                       | wide                                         |   10 |   133 ns |  134 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |   794 µs |  900 µs |    7.94 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |   799 µs |  958 µs |    7.99 µs |
| resolve(), warm                                                       | wide                                         |  100 |   127 ns |  129 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 |  8.51 ms | 10.1 ms |    8.51 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 |  7.86 ms | 8.90 ms |    7.86 µs |
| resolve(), warm                                                       | wide                                         | 1000 |   125 ns |  134 ns |            |
| resolve(), cold                                                       | deep                                         |   10 |  80.6 µs | 84.5 µs |    8.06 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 |  70.7 µs | 71.6 µs |    7.07 µs |
| resolve(), warm                                                       | deep                                         |   10 |   122 ns |  130 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |   883 µs | 1.17 ms |    8.83 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |   826 µs | 2.31 ms |    8.26 µs |
| resolve(), warm                                                       | deep                                         |  100 |   127 ns |  128 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 |  12.8 ms | 17.7 ms |    12.8 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 |  11.7 ms | 12.8 ms |    11.7 µs |
| resolve(), warm                                                       | deep                                         | 1000 |   124 ns |  125 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 |   104 µs |  109 µs |    10.4 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 |  88.9 µs | 92.4 µs |    8.89 µs |
| resolve(), warm                                                       | mixed                                        |   10 |   127 ns |  134 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 |   965 µs |  983 µs |    9.65 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |   890 µs |  911 µs |    8.90 µs |
| resolve(), warm                                                       | mixed                                        |  100 |   126 ns |  129 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 |  9.27 ms | 10.2 ms |    9.27 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 |  8.69 ms | 9.91 ms |    8.69 µs |
| resolve(), warm                                                       | mixed                                        | 1000 |   131 ns |  135 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 |   137 µs |  152 µs |    13.7 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 |   118 µs |  126 µs |    11.8 µs |
| resolve(), cold                                                       | wide, strings                                |  100 |  1.36 ms | 1.50 ms |    13.6 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 |  1.32 ms | 1.55 ms |    13.2 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 |  15.9 ms | 21.2 ms |    15.9 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 |  12.5 ms | 15.0 ms |    12.5 µs |
| resolve(), cold                                                       | deep, strings                                |   10 |   128 µs |  138 µs |    12.8 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 |   113 µs |  124 µs |    11.3 µs |
| resolve(), cold                                                       | deep, strings                                |  100 |  1.62 ms | 6.67 ms |    16.2 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 |  1.55 ms | 1.91 ms |    15.5 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 |  23.1 ms | 26.1 ms |    23.1 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 |  21.7 ms | 25.0 ms |    21.7 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |   246 µs |  299 µs |    24.6 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 |   209 µs |  245 µs |    20.9 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 |  2.27 ms | 4.83 ms |    22.7 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 |  2.20 ms | 3.32 ms |    22.0 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 |  19.5 ms | 22.3 ms |    19.5 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 |  19.1 ms | 20.5 ms |    19.1 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |   258 µs |  313 µs |    25.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 |  2.06 µs | 5.84 µs |     206 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |   256 µs |  311 µs |    25.6 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 |  1.57 ms | 1.68 ms |    15.7 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 |  14.5 µs | 21.0 µs |     145 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 |  1.56 ms | 1.67 ms |    15.6 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 |  14.8 ms | 15.3 ms |    14.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |   192 µs |  301 µs |     192 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 |  14.6 ms | 15.0 ms |    14.6 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 |  1.01 ms | 1.28 ms |     101 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 |  2.00 µs | 5.81 µs |     200 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 |  1.01 ms | 1.28 ms |     101 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 |  10.2 ms | 16.6 ms |     102 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 |  16.0 µs | 26.4 µs |     160 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 |  10.1 ms | 16.6 ms |     101 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 |   101 ms |  120 ms |     101 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |   260 µs |  339 µs |     260 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 |   101 ms |  120 ms |     101 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |   114 ms |  116 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 |  90.0 ms | 90.2 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 |  24.8 ms | 27.0 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      |  9.55 µs | 9.75 µs |            |
| call of the injected function                                         | 2 clients                                    |      |   130 ns |  133 ns |            |
| call of the plain function                                            | 2 clients                                    |      |  41.4 ns | 41.9 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 |   120 µs |  131 µs |    12.0 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 |  1.04 ms | 1.30 ms |    10.4 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 |  10.9 ms | 12.2 ms |    10.9 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 |   192 µs |  563 µs |    19.2 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 |  1.68 ms | 2.16 ms |    16.8 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 |  17.3 ms | 18.2 ms |    17.3 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 |  1.45 ms | 1.71 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 |  88.0 µs | 92.1 µs |    8.80 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 |  2.32 ms | 2.77 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |   906 µs | 1.03 ms |    9.06 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 |  11.0 ms | 11.4 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 |  9.45 ms | 10.1 ms |    9.45 µs |
| one request                                                           | a client through nuke-di                     |      |   106 µs |  109 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |   106 µs |  111 µs |            |
| one request                                                           | no dependencies                              |      | 100.0 µs |  104 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      |  37.0 ms | 39.8 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |   194 ms |  211 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |   166 ms |  178 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |   580 kB |  656 kB |      580 B |

### Python 3.13.14

nuke-di 1.9.0 · CPython 3.13.14 · macOS-26.6.2-arm64-arm-64bit-Mach-O · commit 8a414d6 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |     p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 89.6 µs | 99.6 µs |    8.96 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 74.7 µs | 81.5 µs |    7.47 µs |
| resolve(), warm                                                       | wide                                         |   10 |  111 ns |  118 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  811 µs |  917 µs |    8.11 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  752 µs |  770 µs |    7.52 µs |
| resolve(), warm                                                       | wide                                         |  100 |  111 ns |  111 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 8.63 ms | 9.58 ms |    8.63 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 8.08 ms | 9.62 ms |    8.08 µs |
| resolve(), warm                                                       | wide                                         | 1000 |  110 ns |  114 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 87.8 µs | 93.1 µs |    8.78 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 72.5 µs | 75.5 µs |    7.25 µs |
| resolve(), warm                                                       | deep                                         |   10 |  107 ns |  108 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  837 µs |  925 µs |    8.37 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  793 µs |  808 µs |    7.93 µs |
| resolve(), warm                                                       | deep                                         |  100 |  109 ns |  112 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 12.4 ms | 12.9 ms |    12.4 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 11.8 ms | 13.5 ms |    11.8 µs |
| resolve(), warm                                                       | deep                                         | 1000 |  108 ns |  112 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 |  110 µs |  114 µs |    11.0 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 94.1 µs | 99.6 µs |    9.41 µs |
| resolve(), warm                                                       | mixed                                        |   10 |  111 ns |  115 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 | 1.00 ms | 1.23 ms |    10.0 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  931 µs |  956 µs |    9.31 µs |
| resolve(), warm                                                       | mixed                                        |  100 |  111 ns |  121 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 10.1 ms | 10.8 ms |    10.1 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 9.52 ms | 10.8 ms |    9.52 µs |
| resolve(), warm                                                       | mixed                                        | 1000 |  113 ns |  115 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 |  170 µs |  204 µs |    17.0 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 |  149 µs |  179 µs |    14.9 µs |
| resolve(), cold                                                       | wide, strings                                |  100 | 1.62 ms | 1.71 ms |    16.2 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 | 1.58 ms | 1.72 ms |    15.8 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 17.3 ms | 22.0 ms |    17.3 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 17.0 ms | 19.9 ms |    17.0 µs |
| resolve(), cold                                                       | deep, strings                                |   10 |  167 µs |  184 µs |    16.7 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 |  159 µs |  170 µs |    15.9 µs |
| resolve(), cold                                                       | deep, strings                                |  100 | 1.87 ms | 5.40 ms |    18.7 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 | 1.86 ms | 2.60 ms |    18.6 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 21.5 ms | 26.6 ms |    21.5 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 21.2 ms | 29.7 ms |    21.2 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  297 µs |  317 µs |    29.7 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 |  262 µs |  279 µs |    26.2 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 2.63 ms | 2.82 ms |    26.3 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 | 2.72 ms | 8.63 ms |    27.2 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 27.4 ms | 32.4 ms |    27.4 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 23.1 ms | 27.7 ms |    23.1 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  248 µs |  281 µs |    24.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 1.96 µs | 2.34 µs |     196 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  246 µs |  279 µs |    24.6 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 1.47 ms | 1.53 ms |    14.7 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 13.5 µs | 14.3 µs |     135 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 1.46 ms | 1.51 ms |    14.6 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 13.2 ms | 14.2 ms |    13.2 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  132 µs |  150 µs |     132 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 13.1 ms | 14.0 ms |    13.1 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 |  984 µs | 1.08 ms |    98.4 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.00 µs | 2.56 µs |     200 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 |  982 µs | 1.08 ms |    98.2 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 10.2 ms | 11.2 ms |     102 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 15.6 µs | 18.9 µs |     156 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 10.2 ms | 11.2 ms |     102 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 |  101 ms |  112 ms |     101 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  294 µs |  335 µs |     294 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 |  101 ms |  112 ms |     101 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  115 ms |  115 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 89.1 ms | 90.0 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 25.4 ms | 27.0 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 9.70 µs | 11.9 µs |            |
| call of the injected function                                         | 2 clients                                    |      |  105 ns |  107 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 37.4 ns | 40.2 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 |  125 µs |  140 µs |    12.5 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 | 1.04 ms | 1.08 ms |    10.4 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 10.4 ms | 11.3 ms |    10.4 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 |  182 µs |  201 µs |    18.2 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 | 1.66 ms | 1.94 ms |    16.6 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 16.8 ms | 19.1 ms |    16.8 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 | 1.70 ms | 1.85 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 88.2 µs | 93.9 µs |    8.82 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 | 2.66 ms | 2.99 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  931 µs | 1.03 ms |    9.31 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 11.0 ms | 12.5 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 8.93 ms | 9.25 ms |    8.93 µs |
| one request                                                           | a client through nuke-di                     |      |  107 µs |  124 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  105 µs |  110 µs |            |
| one request                                                           | no dependencies                              |      |  101 µs |  106 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 35.8 ms | 49.6 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  183 ms |  196 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  167 ms |  182 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  596 kB |  672 kB |      596 B |

### Python 3.14.6

nuke-di 1.9.0 · CPython 3.14.6 · macOS-26.6.2-arm64-arm-64bit-Mach-O · commit 8a414d6 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |      p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|---------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 89.5 µs |   105 µs |    8.95 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 81.1 µs |   104 µs |    8.11 µs |
| resolve(), warm                                                       | wide                                         |   10 |  123 ns |   127 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  848 µs |  1.01 ms |    8.48 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  749 µs |   808 µs |    7.49 µs |
| resolve(), warm                                                       | wide                                         |  100 |  119 ns |   119 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 8.65 ms |  9.46 ms |    8.65 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 9.04 ms |  12.1 ms |    9.04 µs |
| resolve(), warm                                                       | wide                                         | 1000 |  123 ns |   133 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 90.1 µs |   129 µs |    9.01 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 73.2 µs |  75.0 µs |    7.32 µs |
| resolve(), warm                                                       | deep                                         |   10 |  117 ns |   124 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  912 µs |  1.05 ms |    9.12 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  781 µs |   892 µs |    7.81 µs |
| resolve(), warm                                                       | deep                                         |  100 |  119 ns |   120 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 12.3 ms |  14.2 ms |    12.3 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 12.1 ms |  14.5 ms |    12.1 µs |
| resolve(), warm                                                       | deep                                         | 1000 |  128 ns |   131 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 |  119 µs |   129 µs |    11.9 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 95.9 µs |   108 µs |    9.59 µs |
| resolve(), warm                                                       | mixed                                        |   10 |  126 ns |   131 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 | 1.10 ms |  1.15 ms |    11.0 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  987 µs |  1.03 ms |    9.87 µs |
| resolve(), warm                                                       | mixed                                        |  100 |  120 ns |   128 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 10.8 ms |  12.1 ms |    10.8 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 9.53 ms |  10.8 ms |    9.53 µs |
| resolve(), warm                                                       | mixed                                        | 1000 |  118 ns |   118 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 |  174 µs |   279 µs |    17.4 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 |  140 µs |   170 µs |    14.0 µs |
| resolve(), cold                                                       | wide, strings                                |  100 | 1.68 ms |  2.46 ms |    16.8 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 | 1.59 ms |  2.87 ms |    15.9 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 25.1 ms |  30.4 ms |    25.1 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 26.6 ms |  28.9 ms |    26.6 µs |
| resolve(), cold                                                       | deep, strings                                |   10 |  180 µs |   207 µs |    18.0 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 |  165 µs |   181 µs |    16.5 µs |
| resolve(), cold                                                       | deep, strings                                |  100 | 1.78 ms |  3.05 ms |    17.8 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 | 1.69 ms |  2.58 ms |    16.9 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 31.7 ms |  34.7 ms |    31.7 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 30.6 ms |  36.7 ms |    30.6 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  279 µs |   332 µs |    27.9 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 |  260 µs |   281 µs |    26.0 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 2.79 ms |  4.84 ms |    27.9 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 | 2.50 ms |  4.50 ms |    25.0 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 37.5 ms |  50.3 ms |    37.5 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 36.1 ms |  39.2 ms |    36.1 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  299 µs |   359 µs |    29.9 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 2.40 µs |  8.44 µs |     240 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  294 µs |   357 µs |    29.4 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 1.54 ms |  1.66 ms |    15.4 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 17.3 µs |  21.8 µs |     173 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 1.52 ms |  1.65 ms |    15.2 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 12.7 ms |  14.1 ms |    12.7 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  218 µs |   317 µs |     218 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 12.5 ms |  13.9 ms |    12.5 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 |  949 µs |   974 µs |    94.9 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.21 µs |  2.75 µs |     221 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 |  947 µs |   972 µs |    94.7 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 9.19 ms |  9.70 ms |    91.9 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 16.0 µs |  20.0 µs |     160 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 9.17 ms |  9.68 ms |    91.7 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 | 92.6 ms |  99.4 ms |    92.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  214 µs |   421 µs |     214 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 | 92.4 ms |  99.2 ms |    92.4 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  115 ms |   116 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 89.1 ms |  89.9 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 25.3 ms |  28.0 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 9.80 µs |  10.1 µs |            |
| call of the injected function                                         | 2 clients                                    |      |  115 ns |   124 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 37.0 ns |  37.5 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 |  118 µs |   147 µs |    11.8 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 | 1.00 ms |  1.35 ms |    10.0 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 10.2 ms |  11.1 ms |    10.2 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 |  184 µs |   212 µs |    18.4 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 | 1.69 ms |  1.88 ms |    16.9 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 18.2 ms |  20.1 ms |    18.2 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 | 1.60 ms |  1.84 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 97.8 µs |   108 µs |    9.78 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 | 2.54 ms |  2.85 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  996 µs |  1.13 ms |    9.96 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 11.5 ms |  13.5 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 9.43 ms | 10.00 ms |    9.43 µs |
| one request                                                           | a client through nuke-di                     |      |  118 µs |   126 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  116 µs |   122 µs |            |
| one request                                                           | no dependencies                              |      |  109 µs |   117 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 35.4 ms |  37.7 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  174 ms |   191 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  161 ms |   176 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  596 kB |   672 kB |      596 B |
