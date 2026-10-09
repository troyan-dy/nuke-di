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

The baseline was taken on an Apple M2 Pro, macOS 26.6.2 (arm64), `nuke-di` 1.11.1 at commit `bd9241f`,
Python 3.11.7, 3.12.5, 3.13.14 and 3.14.6, each in a fresh `uv` environment from `uv.lock`, with
`N = 10, 100, 1000` and 20 repeats. It is the state after the performance changes of 1.9.1–1.11.1: the
per-class cache of what `__init__` takes and its reader from `__code__`
([#29](https://github.com/troyan-dy/nuke-di/issues/29), [#36](https://github.com/troyan-dy/nuke-di/issues/36)),
the cycle check with a set ([#33](https://github.com/troyan-dy/nuke-di/issues/33)), `asyncio.timeout()` instead
of `asyncio.wait_for()` ([#30](https://github.com/troyan-dy/nuke-di/issues/30)), one lock per container around
`resolve()` ([#32](https://github.com/troyan-dy/nuke-di/issues/32)), the resolve without recursion
([#35](https://github.com/troyan-dy/nuke-di/issues/35)) and the client check of
[#55](https://github.com/troyan-dy/nuke-di/issues/55) moved after the cache lookup. The "before" figures are the
1.9.0 baseline at commit `8a414d6`, in the git history of `docs/benchmarks/`.

- **`resolve()` costs 3.7–6.9 µs per client** with real type hints on every version and grows linearly:
  100 clients in 0.37–0.69 ms, 1000 in 4.2–5.5 ms, against 7–14 µs per client and 8–14 ms for 1000 in
  1.9.0. The chain is no longer the most expensive shape: a chain of 1000 clients resolves in 4.2–5.1 ms
  instead of 12.3–13.8 ms, under the default recursion limit on every version. The "200 clients in about
  2 ms" of [#16](https://github.com/troyan-dy/nuke-di/issues/16) is now about 1 ms.
- **A second container costs 1.1–1.7 µs per client**, with both kinds of hints: the classes resolved once in
  the process have what their `__init__` takes cached, string annotations evaluated, so the container only
  builds the instances. That is 2.5–4.6 times below the first container with real type hints and 4.6–21
  times with strings, 0.11–0.17 ms for 100 clients and 1.3–1.7 ms for 1000; in 1.9.0 the second container
  cost the same as the first. It is what every test of a session pays after the first one.
- **String annotations cost 1.6–5.4 times the real-type figure on the first container**: 7–29 µs per
  client, against 11–38 µs in 1.9.0. The ratio is 1.6–2.2 on 3.11, 1.9–3.2 on 3.12, 2.3–4.6 on 3.13 and
  2.5–5.4 on 3.14, and the mixed tree pays the most, with two or three hints per class against one:
  `get_type_hints()` compiles and evaluates every string once per class, and the `annotationlib` of 3.14
  does more per string. The ratio is above that of 1.9.0 (1.3–3.5) because the cache took more off the rest
  of a cold `resolve()` than off the strings, which are evaluated once either way. Most code bases have
  `from __future__ import annotations`, so this is the figure their startup pays, and the second container
  above is the one their tests pay.
- **A warm `resolve()`, the singleton cache hit, is 82–109 ns**, against 100–135 ns in 1.9.0: a dictionary
  lookup after the `connected` check, without the lock of [#32](https://github.com/troyan-dy/nuke-di/issues/32)
  and before the client check of [#55](https://github.com/troyan-dy/nuke-di/issues/55), which 1.11.0 put in
  front of it at about 60 ns a call.
- **`connect()` and `disconnect()` cost 9–15 µs per client in a layer of 100 or more (21–25 µs in a layer of
  10) and about 0.1 ms per layer**, all of it scheduling: the clients' own coroutines take 0.14–0.3 µs each.
  3.11 is level with the other versions now: `asyncio.timeout()`
  ([#30](https://github.com/troyan-dy/nuke-di/issues/30)) took the task per client off it, and the chain of
  1000 clients connects and disconnects in 100 ms instead of 211 ms, the layer of 1000 in 12.2 ms instead of
  19.4 ms; the other versions take 98–106 ms and 9.1–12.2 ms. Not a hot spot: a real `connect()` takes
  milliseconds, a hundred to a thousand times more than its scheduling.
- **The application connects and disconnects in 110.5–112.1 ms of wall time against a critical path of
  86.9–87.6 ms**: 23.6–24.3 ms, 21%, is lost at the layer barriers, where `Repository` and `Users` wait for
  `Kafka` although `Postgres` connected 30 ms earlier. The figure is the same on every version, because
  it is the clients' sleeps and not the scheduling; it is what a schedule by dependency
  ([#28](https://github.com/troyan-dy/nuke-di/issues/28)) would recover.
- **`inject()` takes 8–10 µs** to bind a function with two clients. The `partial` it returns adds
  45–90 ns to a call that takes 36–39 ns without it.
- **A `NotSingletonClient` costs 1.2–1.5 µs more per consumer** than the shared instance of a `Client` at 100
  and 1000 consumers, against 5–8 µs in 1.9.0: a fresh instance is built from the cached arguments of its
  class.
- **The test cycle is `resolve()` plus the mock.** The autospec of `mock()` costs 0.7–1 ms, against
  1.3–1.6 ms in 1.9.0; `override()` with an instance costs the same as a `resolve()` in a second container,
  0.14–0.16 ms for 100 clients.
- **A FastAPI handler that takes a client through `nuke-di` costs the same as one with a plain
  `Depends()`**: 103–107 µs per request for both, within 3% of each other, and 5–14 µs above a handler
  without dependencies. The ASGI stack is the cost, not the injection.
- **`import nuke_di` takes 28–36 ms**, as in 1.9.0, about 20 ms of it `asyncio` and 5 ms `logging`, both
  imported through `nuke_di.clients`; `nuke_di.core` itself is 3 ms. `nuke_di.fastapi` adds 3–18 ms on top of
  `fastapi`'s 141–175 ms, a figure as noisy as a fresh interpreter is. This is the figure the lazy-import work
  in [#14](https://github.com/troyan-dy/nuke-di/issues/14) moves.
- **A resolved client takes about 580 bytes**: 575–590 kB for the 1000-client mixed tree, as in 1.9.0.
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

nuke-di 1.11.1 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit bd9241f · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |     p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 48.2 µs | 66.6 µs |    4.82 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 13.4 µs | 15.4 µs |    1.34 µs |
| resolve(), warm                                                       | wide                                         |   10 | 93.2 ns | 97.6 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  407 µs |  472 µs |    4.07 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  137 µs |  160 µs |    1.37 µs |
| resolve(), warm                                                       | wide                                         |  100 | 88.0 ns | 88.9 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 4.32 ms | 4.71 ms |    4.32 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 1.71 ms | 2.40 ms |    1.71 µs |
| resolve(), warm                                                       | wide                                         | 1000 | 84.9 ns | 89.3 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 43.2 µs | 47.5 µs |    4.32 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 12.7 µs | 15.1 µs |    1.27 µs |
| resolve(), warm                                                       | deep                                         |   10 | 90.2 ns |  111 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  370 µs |  397 µs |    3.70 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  129 µs |  135 µs |    1.29 µs |
| resolve(), warm                                                       | deep                                         |  100 | 84.8 ns | 85.3 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 4.18 ms | 4.83 ms |    4.18 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 1.39 ms | 1.79 ms |    1.39 µs |
| resolve(), warm                                                       | deep                                         | 1000 | 91.6 ns |  104 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 | 56.9 µs | 60.9 µs |    5.69 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 15.1 µs | 22.3 µs |    1.51 µs |
| resolve(), warm                                                       | mixed                                        |   10 | 89.6 ns |  102 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 |  513 µs |  605 µs |    5.13 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  148 µs |  156 µs |    1.48 µs |
| resolve(), warm                                                       | mixed                                        |  100 | 90.3 ns | 95.1 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 4.85 ms | 5.15 ms |    4.85 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 1.53 ms | 1.97 ms |    1.53 µs |
| resolve(), warm                                                       | mixed                                        | 1000 | 91.6 ns |  104 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 | 75.5 µs | 86.4 µs |    7.55 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 | 13.3 µs | 14.4 µs |    1.33 µs |
| resolve(), cold                                                       | wide, strings                                |  100 |  799 µs |  932 µs |    7.99 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 |  158 µs |  207 µs |    1.58 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 7.87 ms | 16.6 ms |    7.87 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 1.71 ms | 2.11 ms |    1.71 µs |
| resolve(), cold                                                       | deep, strings                                |   10 | 76.6 µs | 99.4 µs |    7.66 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 | 12.6 µs | 15.6 µs |    1.26 µs |
| resolve(), cold                                                       | deep, strings                                |  100 |  699 µs |  772 µs |    6.99 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 |  129 µs |  140 µs |    1.29 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 8.12 ms | 8.66 ms |    8.12 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 1.44 ms | 1.80 ms |    1.44 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  124 µs |  159 µs |    12.4 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 | 14.7 µs | 17.2 µs |    1.47 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 1.10 ms | 1.29 ms |    11.0 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 |  149 µs |  164 µs |    1.49 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 10.5 ms | 11.1 ms |    10.5 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 1.51 ms | 2.21 ms |    1.51 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  248 µs |  274 µs |    24.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 2.63 µs | 3.13 µs |     263 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  246 µs |  270 µs |    24.6 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 1.32 ms | 1.63 ms |    13.2 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 22.1 µs | 27.0 µs |     221 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 1.30 ms | 1.61 ms |    13.0 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 12.2 ms | 13.0 ms |    12.2 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  220 µs |  317 µs |     220 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 11.9 ms | 12.7 ms |    11.9 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 |  953 µs | 1.02 ms |    95.3 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.10 µs | 2.67 µs |     210 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 |  951 µs | 1.02 ms |    95.1 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 9.51 ms | 9.87 ms |    95.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 17.9 µs | 22.3 µs |     179 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 9.49 ms | 9.85 ms |    94.9 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 |  100 ms |  106 ms |     100 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  256 µs |  438 µs |     256 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 |  100 ms |  106 ms |     100 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  112 ms |  113 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 87.6 ms | 88.8 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 24.3 ms | 25.7 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 8.30 µs | 12.1 µs |            |
| call of the injected function                                         | 2 clients                                    |      | 82.0 ns | 83.0 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 36.4 ns | 37.7 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 | 65.5 µs | 99.6 µs |    6.55 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 |  551 µs |  713 µs |    5.51 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 5.57 ms | 6.31 ms |    5.57 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 | 75.4 µs | 87.7 µs |    7.54 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 |  674 µs |  896 µs |    6.74 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 6.85 ms | 7.71 ms |    6.85 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 |  706 µs |  914 µs |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 18.2 µs | 22.9 µs |    1.82 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 |  862 µs | 1.11 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  152 µs |  159 µs |    1.52 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 2.30 ms | 2.90 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 1.49 ms | 1.65 ms |    1.49 µs |
| one request                                                           | a client through nuke-di                     |      |  105 µs |  107 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  105 µs |  113 µs |            |
| one request                                                           | no dependencies                              |      | 98.7 µs |  103 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 27.8 ms | 29.4 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  145 ms |  152 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  142 ms |  158 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  583 kB |  659 kB |      583 B |

### Python 3.12.5

nuke-di 1.11.1 · CPython 3.12.5 · macOS-26.6.2-arm64-arm-64bit · commit bd9241f · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |     p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 46.0 µs | 57.9 µs |    4.60 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 12.6 µs | 15.2 µs |    1.26 µs |
| resolve(), warm                                                       | wide                                         |   10 |  108 ns |  108 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  389 µs |  470 µs |    3.89 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  128 µs |  140 µs |    1.28 µs |
| resolve(), warm                                                       | wide                                         |  100 |  104 ns |  107 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 4.21 ms | 4.78 ms |    4.21 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 1.70 ms | 2.10 ms |    1.70 µs |
| resolve(), warm                                                       | wide                                         | 1000 |  106 ns |  110 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 42.7 µs | 57.8 µs |    4.27 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 13.3 µs | 17.4 µs |    1.33 µs |
| resolve(), warm                                                       | deep                                         |   10 |  109 ns |  162 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  428 µs |  529 µs |    4.28 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  140 µs |  151 µs |    1.40 µs |
| resolve(), warm                                                       | deep                                         |  100 |  109 ns |  121 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 4.45 ms | 5.06 ms |    4.45 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 1.41 ms | 1.68 ms |    1.41 µs |
| resolve(), warm                                                       | deep                                         | 1000 |  104 ns |  115 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 | 54.4 µs | 65.3 µs |    5.44 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 15.0 µs | 16.2 µs |    1.50 µs |
| resolve(), warm                                                       | mixed                                        |   10 |  105 ns |  134 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 |  523 µs |  729 µs |    5.23 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  167 µs |  245 µs |    1.67 µs |
| resolve(), warm                                                       | mixed                                        |  100 |  104 ns |  108 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 4.57 ms | 5.22 ms |    4.57 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 1.53 ms | 1.69 ms |    1.53 µs |
| resolve(), warm                                                       | mixed                                        | 1000 |  103 ns |  113 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 | 90.6 µs | 95.9 µs |    9.06 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 | 12.5 µs | 15.4 µs |    1.25 µs |
| resolve(), cold                                                       | wide, strings                                |  100 |  881 µs | 1.05 ms |    8.81 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 |  137 µs |  187 µs |    1.37 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 9.87 ms | 11.1 ms |    9.87 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 1.58 ms | 1.92 ms |    1.58 µs |
| resolve(), cold                                                       | deep, strings                                |   10 | 86.6 µs |  116 µs |    8.66 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 | 13.2 µs | 13.8 µs |    1.32 µs |
| resolve(), cold                                                       | deep, strings                                |  100 |  824 µs | 1.15 ms |    8.24 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 |  137 µs |  145 µs |    1.37 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 10.9 ms | 12.0 ms |    10.9 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 1.45 ms | 1.91 ms |    1.45 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  172 µs |  259 µs |    17.2 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 | 15.4 µs | 20.3 µs |    1.54 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 1.61 ms | 1.83 ms |    16.1 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 |  163 µs |  176 µs |    1.63 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 14.5 ms | 15.4 ms |    14.5 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 1.61 ms | 2.16 ms |    1.61 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  240 µs |  285 µs |    24.0 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 2.33 µs | 2.67 µs |     233 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  238 µs |  282 µs |    23.8 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 1.46 ms | 1.62 ms |    14.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 16.7 µs | 21.1 µs |     167 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 1.45 ms | 1.60 ms |    14.5 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 12.2 ms | 13.0 ms |    12.2 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  165 µs |  244 µs |     165 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 12.1 ms | 12.9 ms |    12.1 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 | 1.03 ms | 1.11 ms |     103 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.33 µs | 3.13 µs |     233 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 | 1.03 ms | 1.11 ms |     103 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 10.1 ms | 10.8 ms |     101 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 16.1 µs | 19.3 µs |     161 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 10.0 ms | 10.8 ms |     100 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 |  101 ms |  104 ms |     101 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  181 µs |  302 µs |     181 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 |  101 ms |  104 ms |     101 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  112 ms |  114 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 87.6 ms | 88.1 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 24.3 ms | 27.1 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 9.07 µs | 12.8 µs |            |
| call of the injected function                                         | 2 clients                                    |      |  126 ns |  128 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 39.5 ns | 40.0 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 | 62.0 µs | 65.9 µs |    6.20 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 |  526 µs |  655 µs |    5.26 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 5.62 ms | 6.62 ms |    5.62 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 | 72.7 µs | 85.4 µs |    7.27 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 |  679 µs |  865 µs |    6.79 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 6.97 ms | 7.58 ms |    6.97 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 |  692 µs |  834 µs |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 18.6 µs | 23.8 µs |    1.86 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 |  862 µs |  975 µs |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  160 µs |  192 µs |    1.60 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 2.53 ms | 3.25 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 1.60 ms | 1.90 ms |    1.60 µs |
| one request                                                           | a client through nuke-di                     |      |  105 µs |  110 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  103 µs |  108 µs |            |
| one request                                                           | no dependencies                              |      | 91.7 µs | 94.9 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 35.5 ms | 37.0 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  177 ms |  222 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  175 ms |  195 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  574 kB |  651 kB |      574 B |

### Python 3.13.14

nuke-di 1.11.1 · CPython 3.13.14 · macOS-26.6.2-arm64-arm-64bit-Mach-O · commit bd9241f · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |      p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|---------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 52.0 µs |  85.1 µs |    5.20 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 12.2 µs |  13.1 µs |    1.22 µs |
| resolve(), warm                                                       | wide                                         |   10 | 93.2 ns |  95.7 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  438 µs |   551 µs |    4.38 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  121 µs |   132 µs |    1.21 µs |
| resolve(), warm                                                       | wide                                         |  100 | 91.4 ns |  96.2 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 4.78 ms |  7.34 ms |    4.78 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 1.55 ms |  1.89 ms |    1.55 µs |
| resolve(), warm                                                       | wide                                         | 1000 | 92.5 ns |   102 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 44.1 µs |  54.2 µs |    4.41 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 12.3 µs |  13.4 µs |    1.23 µs |
| resolve(), warm                                                       | deep                                         |   10 | 90.6 ns |   101 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  435 µs |   558 µs |    4.35 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  136 µs |   198 µs |    1.36 µs |
| resolve(), warm                                                       | deep                                         |  100 | 86.7 ns | 100.0 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 4.76 ms |  5.67 ms |    4.76 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 1.36 ms |  1.88 ms |    1.36 µs |
| resolve(), warm                                                       | deep                                         | 1000 | 89.7 ns |  92.1 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 | 58.2 µs |  89.6 µs |    5.82 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 14.6 µs |  28.2 µs |    1.46 µs |
| resolve(), warm                                                       | mixed                                        |   10 | 86.1 ns |  86.4 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 |  560 µs |   665 µs |    5.60 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  147 µs |   156 µs |    1.47 µs |
| resolve(), warm                                                       | mixed                                        |  100 | 87.6 ns |  97.3 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 5.15 ms |  5.68 ms |    5.15 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 1.47 ms |  1.96 ms |    1.47 µs |
| resolve(), warm                                                       | mixed                                        | 1000 | 93.2 ns |   107 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 |  117 µs |   161 µs |    11.7 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 | 11.4 µs |  12.9 µs |    1.14 µs |
| resolve(), cold                                                       | wide, strings                                |  100 | 1.21 ms |  1.36 ms |    12.1 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 |  120 µs |   142 µs |    1.20 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 12.2 ms |  13.5 ms |    12.2 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 1.53 ms |  1.97 ms |    1.53 µs |
| resolve(), cold                                                       | deep, strings                                |   10 |  118 µs |   153 µs |    11.8 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 | 12.2 µs |  14.8 µs |    1.22 µs |
| resolve(), cold                                                       | deep, strings                                |  100 | 1.30 ms |  1.91 ms |    13.0 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 |  128 µs |   160 µs |    1.28 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 14.7 ms |  18.6 ms |    14.7 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 1.57 ms |  5.02 ms |    1.57 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  265 µs |  1.97 ms |    26.5 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 | 14.6 µs |  26.5 µs |    1.46 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 2.22 ms |  3.89 ms |    22.2 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 |  146 µs |   192 µs |    1.46 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 17.6 ms |  47.7 ms |    17.6 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 1.49 ms |  2.00 ms |    1.49 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  231 µs |   244 µs |    23.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 2.25 µs |  2.73 µs |     225 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  228 µs |   242 µs |    22.8 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 1.28 ms |  2.60 ms |    12.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 14.3 µs |  18.3 µs |     143 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 1.26 ms |  2.58 ms |    12.6 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 10.4 ms |  12.3 ms |    10.4 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  146 µs |   181 µs |     146 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 10.2 ms |  12.1 ms |    10.2 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 |  963 µs |  1.01 ms |    96.3 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.00 µs |  2.55 µs |     200 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 |  961 µs |  1.01 ms |    96.1 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 10.7 ms |  12.5 ms |     107 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 17.0 µs |  27.7 µs |     170 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 10.7 ms |  12.4 ms |     107 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 |  106 ms |   204 ms |     106 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  277 µs |   399 µs |     277 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 |  106 ms |   204 ms |     106 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  110 ms |   111 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 86.9 ms |  87.1 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 23.6 ms |  23.8 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 9.57 µs |  9.83 µs |            |
| call of the injected function                                         | 2 clients                                    |      |  109 ns |   111 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 37.5 ns |  38.1 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 | 65.1 µs |  70.2 µs |    6.51 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 |  525 µs |   555 µs |    5.25 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 5.78 ms |  6.25 ms |    5.78 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 | 74.7 µs |  93.2 µs |    7.47 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 |  644 µs |   770 µs |    6.44 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 7.00 ms |  7.58 ms |    7.00 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 |  892 µs |  1.07 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 18.1 µs |  20.7 µs |    1.81 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 | 1.05 ms |  1.33 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  148 µs |   166 µs |    1.48 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 2.66 ms |  3.31 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 1.63 ms |  1.93 ms |    1.63 µs |
| one request                                                           | a client through nuke-di                     |      |  105 µs |   125 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  106 µs |   110 µs |            |
| one request                                                           | no dependencies                              |      | 99.9 µs |   105 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 36.0 ms |  38.6 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  162 ms |   169 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  144 ms |   152 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  590 kB |   667 kB |      590 B |

### Python 3.14.6

nuke-di 1.11.1 · CPython 3.14.6 · macOS-26.6.2-arm64-arm-64bit-Mach-O · commit bd9241f · N = 10, 100, 1000 · 20 repeats

| Scenario                                                              | Shape                                        |    N |  Median |     p95 | Per client |
|-----------------------------------------------------------------------|----------------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                                       | wide                                         |   10 | 48.0 µs | 50.4 µs |    4.80 µs |
| resolve(), second container, classes seen before                      | wide                                         |   10 | 11.2 µs | 12.3 µs |    1.12 µs |
| resolve(), warm                                                       | wide                                         |   10 | 86.4 ns | 87.4 ns |            |
| resolve(), cold                                                       | wide                                         |  100 |  400 µs |  415 µs |    4.00 µs |
| resolve(), second container, classes seen before                      | wide                                         |  100 |  114 µs |  137 µs |    1.14 µs |
| resolve(), warm                                                       | wide                                         |  100 | 83.8 ns | 87.6 ns |            |
| resolve(), cold                                                       | wide                                         | 1000 | 4.29 ms | 5.23 ms |    4.29 µs |
| resolve(), second container, classes seen before                      | wide                                         | 1000 | 1.38 ms | 1.42 ms |    1.38 µs |
| resolve(), warm                                                       | wide                                         | 1000 | 83.2 ns | 83.5 ns |            |
| resolve(), cold                                                       | deep                                         |   10 | 45.8 µs | 50.5 µs |    4.58 µs |
| resolve(), second container, classes seen before                      | deep                                         |   10 | 11.9 µs | 12.4 µs |    1.19 µs |
| resolve(), warm                                                       | deep                                         |   10 | 82.3 ns | 90.5 ns |            |
| resolve(), cold                                                       | deep                                         |  100 |  416 µs |  507 µs |    4.16 µs |
| resolve(), second container, classes seen before                      | deep                                         |  100 |  122 µs |  128 µs |    1.22 µs |
| resolve(), warm                                                       | deep                                         |  100 | 84.2 ns | 84.6 ns |            |
| resolve(), cold                                                       | deep                                         | 1000 | 5.13 ms | 5.81 ms |    5.13 µs |
| resolve(), second container, classes seen before                      | deep                                         | 1000 | 1.37 ms | 1.77 ms |    1.37 µs |
| resolve(), warm                                                       | deep                                         | 1000 | 83.9 ns | 92.7 ns |            |
| resolve(), cold                                                       | mixed                                        |   10 | 65.9 µs | 85.2 µs |    6.59 µs |
| resolve(), second container, classes seen before                      | mixed                                        |   10 | 14.9 µs | 16.3 µs |    1.49 µs |
| resolve(), warm                                                       | mixed                                        |   10 | 87.2 ns |  108 ns |            |
| resolve(), cold                                                       | mixed                                        |  100 |  685 µs | 1.41 ms |    6.85 µs |
| resolve(), second container, classes seen before                      | mixed                                        |  100 |  148 µs |  208 µs |    1.48 µs |
| resolve(), warm                                                       | mixed                                        |  100 | 87.6 ns | 89.2 ns |            |
| resolve(), cold                                                       | mixed                                        | 1000 | 5.47 ms | 6.47 ms |    5.47 µs |
| resolve(), second container, classes seen before                      | mixed                                        | 1000 | 1.39 ms | 1.92 ms |    1.39 µs |
| resolve(), warm                                                       | mixed                                        | 1000 | 86.7 ns | 96.1 ns |            |
| resolve(), cold                                                       | wide, strings                                |   10 |  118 µs |  149 µs |    11.8 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |   10 | 11.6 µs | 15.3 µs |    1.16 µs |
| resolve(), cold                                                       | wide, strings                                |  100 | 1.08 ms | 1.27 ms |    10.8 µs |
| resolve(), second container, classes seen before                      | wide, strings                                |  100 |  118 µs |  171 µs |    1.18 µs |
| resolve(), cold                                                       | wide, strings                                | 1000 | 20.2 ms | 21.7 ms |    20.2 µs |
| resolve(), second container, classes seen before                      | wide, strings                                | 1000 | 1.60 ms | 2.06 ms |    1.60 µs |
| resolve(), cold                                                       | deep, strings                                |   10 |  128 µs |  169 µs |    12.8 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |   10 | 12.1 µs | 13.1 µs |    1.21 µs |
| resolve(), cold                                                       | deep, strings                                |  100 | 1.27 ms | 1.54 ms |    12.7 µs |
| resolve(), second container, classes seen before                      | deep, strings                                |  100 |  123 µs |  139 µs |    1.23 µs |
| resolve(), cold                                                       | deep, strings                                | 1000 | 21.0 ms | 24.5 ms |    21.0 µs |
| resolve(), second container, classes seen before                      | deep, strings                                | 1000 | 1.33 ms | 1.61 ms |    1.33 µs |
| resolve(), cold                                                       | mixed, strings                               |   10 |  215 µs |  264 µs |    21.5 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |   10 | 14.0 µs | 15.1 µs |    1.40 µs |
| resolve(), cold                                                       | mixed, strings                               |  100 | 2.21 ms | 2.33 ms |    22.1 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               |  100 |  138 µs |  149 µs |    1.38 µs |
| resolve(), cold                                                       | mixed, strings                               | 1000 | 29.4 ms | 58.6 ms |    29.4 µs |
| resolve(), second container, classes seen before                      | mixed, strings                               | 1000 | 1.37 ms | 1.43 ms |    1.37 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |   10 |  211 µs |  232 µs |    21.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |   10 | 2.46 µs | 3.17 µs |     246 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |   10 |  208 µs |  229 µs |    20.8 µs |
| connect() + disconnect()                                              | wide: N in one layer                         |  100 | 1.08 ms | 1.10 ms |    10.8 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         |  100 | 18.6 µs | 19.2 µs |     186 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         |  100 | 1.07 ms | 1.08 ms |    10.7 µs |
| connect() + disconnect()                                              | wide: N in one layer                         | 1000 | 9.09 ms | 10.7 ms |    9.09 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | wide: N in one layer                         | 1000 |  144 µs |  301 µs |     144 ns |
| connect() + disconnect(), overhead above the ideal                    | wide: N in one layer                         | 1000 | 8.95 ms | 10.5 ms |    8.95 µs |
| connect() + disconnect()                                              | deep: N layers                               |   10 | 1.10 ms | 1.24 ms |     110 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |   10 | 2.88 µs | 10.3 µs |     288 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |   10 | 1.10 ms | 1.24 ms |     110 µs |
| connect() + disconnect()                                              | deep: N layers                               |  100 | 9.70 ms | 10.1 ms |    97.0 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               |  100 | 18.8 µs | 20.0 µs |     188 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               |  100 | 9.68 ms | 10.1 ms |    96.8 µs |
| connect() + disconnect()                                              | deep: N layers                               | 1000 | 98.3 ms |  103 ms |    98.3 µs |
| connect() + disconnect(), ideal: coroutines awaited directly          | deep: N layers                               | 1000 |  212 µs |  335 µs |     212 ns |
| connect() + disconnect(), overhead above the ideal                    | deep: N layers                               | 1000 | 98.1 ms |  103 ms |    98.1 µs |
| connect() + disconnect(), wall time                                   | application: 8 clients, connect() of 1–60 ms |    8 |  111 ms |  114 ms |            |
| connect() + disconnect(), ideal: the critical path, no layer barriers | application: 8 clients, connect() of 1–60 ms |    8 | 87.1 ms | 88.2 ms |            |
| connect() + disconnect(), lost at the layer barriers                  | application: 8 clients, connect() of 1–60 ms |    8 | 23.8 ms | 26.1 ms |            |
| inject(), clients resolved                                            | 2 clients                                    |      | 9.58 µs | 13.0 µs |            |
| call of the injected function                                         | 2 clients                                    |      |  116 ns |  117 ns |            |
| call of the plain function                                            | 2 clients                                    |      | 37.0 ns | 37.7 ns |            |
| resolve(), cold                                                       | N consumers of a Client                      |   10 | 68.2 µs |  112 µs |    6.82 µs |
| resolve(), cold                                                       | N consumers of a Client                      |  100 |  536 µs |  557 µs |    5.36 µs |
| resolve(), cold                                                       | N consumers of a Client                      | 1000 | 5.42 ms | 6.70 ms |    5.42 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |   10 | 76.4 µs | 80.7 µs |    7.64 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          |  100 |  665 µs |  829 µs |    6.65 µs |
| resolve(), cold                                                       | N consumers of a NotSingletonClient          | 1000 | 6.90 ms | 7.37 ms |    6.90 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |   10 |  756 µs |  868 µs |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |   10 | 17.0 µs | 20.6 µs |    1.70 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       |  100 |  944 µs | 1.11 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       |  100 |  143 µs |  149 µs |    1.43 µs |
| flush() + mock() + resolve()                                          | mixed, a leaf replaced                       | 1000 | 2.25 ms | 2.77 ms |            |
| override() block + resolve()                                          | mixed, a leaf replaced                       | 1000 | 1.43 ms | 1.46 ms |    1.43 µs |
| one request                                                           | a client through nuke-di                     |      |  107 µs |  116 µs |            |
| one request                                                           | a plain FastAPI Depends()                    |      |  107 µs |  109 µs |            |
| one request                                                           | no dependencies                              |      | 99.5 µs |  104 µs |            |
| import, fresh interpreter                                             | nuke_di                                      |      | 34.5 ms | 41.8 ms |            |
| import, fresh interpreter                                             | nuke_di.fastapi                              |      |  162 ms |  179 ms |            |
| import, fresh interpreter                                             | fastapi                                      |      |  154 ms |  189 ms |            |
| resolve(), tracemalloc peak                                           | mixed                                        | 1000 |  590 kB |  667 kB |      590 B |
