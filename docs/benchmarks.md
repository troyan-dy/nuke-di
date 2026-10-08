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

## What is measured

Every tree is built from classes made with `dataclasses.make_dataclass`, so each client has a real
`__init__` with type hints, as a user would write it. `N` is the number of clients in the tree.

| Scenario | Shape | The figure |
|----------|-------|------------|
| `resolve(), cold` | `wide`: one root that declares `N - 1` clients without dependencies, two layers | `resolve()` of the root on a fresh `Dependencies()` |
| | `deep`: a chain of `N` clients, `N` layers | |
| | `mixed`: a pyramid 1, 2, 4, ... wide from the top, every client depends on two or three of the level below, about log2(N) layers | |
| `resolve(), warm` | the same trees | A second `resolve()` of the same root: the singleton cache hit, independent of `N` |
| `connect() + disconnect()` | `wide`: `N` clients in one layer, connected concurrently; `deep`: `N` layers, connected one after another | One `connect()` and `disconnect()` of the container |
| `..., ideal` | the same | The same clients' `connect()` and `disconnect()` coroutines awaited directly, without the container |
| `..., overhead above the ideal` | the same | The difference of the two, sample by sample: what the scheduling costs |
| `inject(), clients resolved` | 2 clients | `inject()` of a function with two client arguments, the clients resolved already |
| `call of the injected function` / `... plain function` | 2 clients | One call of the `partial` that `inject()` returns, against one call of the function with the clients passed by hand |
| `resolve(), cold`, `N consumers of a Client` / `... NotSingletonClient` | one root, `N` consumers of one `Session` | A fresh `Session` per consumer against one shared instance |
| `flush() + mock() + resolve()` | `mixed`, a leaf replaced | The cycle of a test with the pytest plugin: `flush()`, `mock()` (an autospec mock), `resolve()` of the root |
| `override() block + resolve()` | `mixed`, a leaf replaced | `with override(Leaf, fake): resolve(root)`, the block flushes on exit |
| `one request` | FastAPI, via the `httpx` ASGI transport | One `GET` to a handler that takes a client through `nuke_di.fastapi`, to one with a plain async `Depends()`, and to one without dependencies |
| `import, fresh interpreter` | `nuke_di`, `nuke_di.fastapi`, `fastapi` | The cumulative import time from `python -X importtime` in a new process |
| `resolve(), tracemalloc peak` | `mixed`, the largest `N` | The peak traced memory of `resolve()` on a fresh container |

## How to read the figures

- **Median and p95** of `K` samples; `K = 20` in the baseline. The p95 shows the noise of the machine
  rather than a property of the library: compare medians between versions, and treat a change inside the
  spread between median and p95 as nothing.
- **Per client** is the median divided by `N`, so figures at different `N` are comparable; it is left
  out where it means nothing (a cache hit, the fixed cost of an autospec mock).
- Garbage collection is paused while a sample runs, as `timeit` does, and one untimed warm-up pays the
  one-off costs, e.g. the lazy import of `unittest.mock` by `mock()`.
- `resolve()` recurses once per layer, and the default recursion limit stops a chain at about 400 clients
  on Python 3.11 and 800 on 3.12 and later. The runner raises the limit for its deep trees; a real tree
  of that depth would have to do the same.
- The `connect()` figures include the logging calls of the container (the `nuke_di` logger with no
  handler) and a `ClientTiming` per client, which is what a real startup pays too.

## Findings

The baseline was taken on an Apple M2 Pro, macOS 26.6.2 (arm64), `nuke-di` 1.8.0 at commit `11f5919`,
Python 3.11.7, 3.12.5, 3.13.14 and 3.14.6, each in a fresh `uv` environment from `uv.lock`, with
`N = 10, 100, 1000` and 20 repeats.

- **`resolve()` costs 6–12 µs per client** on every version and grows linearly: 100 clients in under
  1 ms, 1000 in 7–13 ms. The chain is the most expensive shape per client (one recursion level per
  client), the wide tree the cheapest. A warm `resolve()`, the singleton cache hit, is about 100 ns.
  The "200 clients in about 2 ms" of [#16](https://github.com/troyan-dy/nuke-di/issues/16) holds.
- **`connect()` and `disconnect()` cost 12–18 µs per client in a layer and 0.1–0.2 ms per layer**,
  all of it scheduling: the clients' own coroutines take 0.2 µs each. A layer is cheapest on 3.14 and
  most expensive on 3.11, where `asyncio.wait_for()` still creates a task per call, which doubles the
  cost of a chain. Not a hot spot: a real `connect()` takes milliseconds, a hundred to a thousand times
  more than its scheduling.
- **`inject()` takes 8–10 µs** to bind a function with two clients. The `partial` it returns adds
  40–80 ns to a call that takes 40 ns without it.
- **A `NotSingletonClient` costs 5–7 µs more per consumer** than the shared instance of a `Client`.
- **The test cycle is `resolve()` plus the mock.** The autospec of `mock()` costs about 1.3 ms whatever
  the tree; `override()` with an instance costs the same as a plain `resolve()`.
- **A FastAPI handler that takes a client through `nuke-di` costs the same as one with a plain
  `Depends()`**: 102–114 µs per request for both, within the noise, and 5–7 µs above a handler
  without dependencies. The ASGI stack is the cost, not the injection.
- **`import nuke_di` takes 26–35 ms**, 19 ms of it `asyncio` and 5 ms `logging`, both imported through
  `nuke_di.clients`; `nuke_di.core` itself is 2 ms. `nuke_di.fastapi` adds 4–7 ms on top of
  `fastapi`'s 137–166 ms. This is the figure the lazy-import work in
  [#14](https://github.com/troyan-dy/nuke-di/issues/14) moves.
- **A resolved client takes about 400 bytes**: 400 kB for the 1000-client mixed tree.
- Between versions, `resolve()` is within 15% (3.11 the fastest), `connect()` is fastest on 3.14, and
  the request figures of 3.14 are a few percent above the others.

## Baseline

### Python 3.11.7

nuke-di 1.8.0 · CPython 3.11.7 · macOS-26.6.2-arm64-arm-64bit · commit 11f5919 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                     | Shape                               |    N |  Median |     p95 | Per client |
|--------------------------------------------------------------|-------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                              | wide                                |   10 | 61.8 µs | 77.3 µs |    6.18 µs |
| resolve(), warm                                              | wide                                |   10 | 94.3 ns | 98.2 ns |            |
| resolve(), cold                                              | wide                                |  100 |  649 µs |  738 µs |    6.49 µs |
| resolve(), warm                                              | wide                                |  100 |  101 ns |  101 ns |            |
| resolve(), cold                                              | wide                                | 1000 | 7.17 ms | 7.69 ms |    7.17 µs |
| resolve(), warm                                              | wide                                | 1000 | 99.0 ns |  120 ns |            |
| resolve(), cold                                              | deep                                |   10 | 61.8 µs | 80.0 µs |    6.18 µs |
| resolve(), warm                                              | deep                                |   10 | 99.7 ns |  115 ns |            |
| resolve(), cold                                              | deep                                |  100 |  782 µs |  985 µs |    7.82 µs |
| resolve(), warm                                              | deep                                |  100 | 95.9 ns | 96.8 ns |            |
| resolve(), cold                                              | deep                                | 1000 | 12.3 ms | 13.4 ms |    12.3 µs |
| resolve(), warm                                              | deep                                | 1000 |  101 ns |  109 ns |            |
| resolve(), cold                                              | mixed                               |   10 | 86.3 µs |  113 µs |    8.63 µs |
| resolve(), warm                                              | mixed                               |   10 | 96.1 ns |  108 ns |            |
| resolve(), cold                                              | mixed                               |  100 |  828 µs |  937 µs |    8.28 µs |
| resolve(), warm                                              | mixed                               |  100 | 97.5 ns |  107 ns |            |
| resolve(), cold                                              | mixed                               | 1000 | 8.04 ms | 8.63 ms |    8.04 µs |
| resolve(), warm                                              | mixed                               | 1000 | 99.3 ns |  158 ns |            |
| connect() + disconnect()                                     | wide: N in one layer                |   10 |  426 µs |  560 µs |    42.6 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                |   10 | 2.56 µs | 3.50 µs |     256 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                |   10 |  423 µs |  557 µs |    42.3 µs |
| connect() + disconnect()                                     | wide: N in one layer                |  100 | 2.11 ms | 2.35 ms |    21.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                |  100 | 17.4 µs | 21.6 µs |     174 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                |  100 | 2.08 ms | 2.33 ms |    20.8 µs |
| connect() + disconnect()                                     | wide: N in one layer                | 1000 | 18.4 ms | 19.2 ms |    18.4 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                | 1000 |  234 µs |  273 µs |     234 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                | 1000 | 18.2 ms | 19.0 ms |    18.2 µs |
| connect() + disconnect()                                     | deep: N layers                      |   10 | 2.40 ms | 3.51 ms |     240 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      |   10 | 3.29 µs | 4.52 µs |     329 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      |   10 | 2.40 ms | 3.51 ms |     240 µs |
| connect() + disconnect()                                     | deep: N layers                      |  100 | 19.8 ms | 20.6 ms |     198 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      |  100 | 19.6 µs | 27.5 µs |     196 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      |  100 | 19.8 ms | 20.6 ms |     198 µs |
| connect() + disconnect()                                     | deep: N layers                      | 1000 |  193 ms |  197 ms |     193 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      | 1000 |  244 µs |  281 µs |     244 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      | 1000 |  193 ms |  196 ms |     193 µs |
| inject(), clients resolved                                   | 2 clients                           |      | 7.96 µs | 8.11 µs |            |
| call of the injected function                                | 2 clients                           |      | 80.7 ns | 81.6 ns |            |
| call of the plain function                                   | 2 clients                           |      | 35.9 ns | 37.2 ns |            |
| resolve(), cold                                              | N consumers of a Client             |   10 | 93.8 µs |  113 µs |    9.38 µs |
| resolve(), cold                                              | N consumers of a Client             |  100 |  810 µs | 1000 µs |    8.10 µs |
| resolve(), cold                                              | N consumers of a Client             | 1000 | 9.15 ms | 9.58 ms |    9.15 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient |   10 |  130 µs |  149 µs |    13.0 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient |  100 | 1.32 ms | 1.49 ms |    13.2 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient | 1000 | 14.9 ms | 15.8 ms |    14.9 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              |   10 | 1.35 ms | 1.59 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              |   10 | 77.0 µs | 98.6 µs |    7.70 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              |  100 | 2.12 ms | 2.56 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              |  100 |  790 µs | 1.05 ms |    7.90 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              | 1000 | 9.81 ms | 10.6 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              | 1000 | 8.17 ms | 8.77 ms |    8.17 µs |
| one request                                                  | a client through nuke-di            |      |  102 µs |  106 µs |            |
| one request                                                  | a plain FastAPI Depends()           |      |  103 µs |  105 µs |            |
| one request                                                  | no dependencies                     |      | 97.0 µs |  101 µs |            |
| import, fresh interpreter                                    | nuke_di                             |      | 26.1 ms | 26.4 ms |            |
| import, fresh interpreter                                    | nuke_di.fastapi                     |      |  141 ms |  146 ms |            |
| import, fresh interpreter                                    | fastapi                             |      |  137 ms |  140 ms |            |
| resolve(), tracemalloc peak                                  | mixed                               | 1000 |  397 kB |  474 kB |      397 B |

### Python 3.12.5

nuke-di 1.8.0 · CPython 3.12.5 · macOS-26.6.2-arm64-arm-64bit · commit 11f5919 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                     | Shape                               |    N |  Median |     p95 | Per client |
|--------------------------------------------------------------|-------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                              | wide                                |   10 | 72.0 µs | 77.9 µs |    7.20 µs |
| resolve(), warm                                              | wide                                |   10 |  128 ns |  132 ns |            |
| resolve(), cold                                              | wide                                |  100 |  716 µs |  939 µs |    7.16 µs |
| resolve(), warm                                              | wide                                |  100 |  122 ns |  147 ns |            |
| resolve(), cold                                              | wide                                | 1000 | 7.76 ms | 8.51 ms |    7.76 µs |
| resolve(), warm                                              | wide                                | 1000 |  128 ns |  128 ns |            |
| resolve(), cold                                              | deep                                |   10 | 68.3 µs | 69.2 µs |    6.83 µs |
| resolve(), warm                                              | deep                                |   10 |  126 ns |  153 ns |            |
| resolve(), cold                                              | deep                                |  100 |  805 µs |  915 µs |    8.05 µs |
| resolve(), warm                                              | deep                                |  100 |  124 ns |  129 ns |            |
| resolve(), cold                                              | deep                                | 1000 | 12.4 ms | 13.1 ms |    12.4 µs |
| resolve(), warm                                              | deep                                | 1000 |  124 ns |  137 ns |            |
| resolve(), cold                                              | mixed                               |   10 | 87.6 µs |  110 µs |    8.76 µs |
| resolve(), warm                                              | mixed                               |   10 |  123 ns |  144 ns |            |
| resolve(), cold                                              | mixed                               |  100 |  873 µs |  950 µs |    8.73 µs |
| resolve(), warm                                              | mixed                               |  100 |  124 ns |  124 ns |            |
| resolve(), cold                                              | mixed                               | 1000 | 8.92 ms | 9.64 ms |    8.92 µs |
| resolve(), warm                                              | mixed                               | 1000 |  120 ns |  128 ns |            |
| connect() + disconnect()                                     | wide: N in one layer                |   10 |  240 µs |  292 µs |    24.0 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                |   10 | 1.83 µs | 2.30 µs |     183 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                |   10 |  238 µs |  291 µs |    23.8 µs |
| connect() + disconnect()                                     | wide: N in one layer                |  100 | 1.53 ms | 1.72 ms |    15.3 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                |  100 | 16.3 µs | 21.0 µs |     163 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                |  100 | 1.51 ms | 1.71 ms |    15.1 µs |
| connect() + disconnect()                                     | wide: N in one layer                | 1000 | 14.4 ms | 15.0 ms |    14.4 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                | 1000 |  168 µs |  224 µs |     168 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                | 1000 | 14.2 ms | 14.8 ms |    14.2 µs |
| connect() + disconnect()                                     | deep: N layers                      |   10 | 1.08 ms | 1.12 ms |     108 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      |   10 | 2.33 µs | 2.59 µs |     233 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      |   10 | 1.08 ms | 1.12 ms |     108 µs |
| connect() + disconnect()                                     | deep: N layers                      |  100 | 10.6 ms | 11.8 ms |     106 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      |  100 | 17.1 µs | 23.9 µs |     171 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      |  100 | 10.6 ms | 11.8 ms |     106 µs |
| connect() + disconnect()                                     | deep: N layers                      | 1000 |  105 ms |  110 ms |     105 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      | 1000 |  169 µs |  196 µs |     169 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      | 1000 |  105 ms |  110 ms |     105 µs |
| inject(), clients resolved                                   | 2 clients                           |      | 9.03 µs | 9.16 µs |            |
| call of the injected function                                | 2 clients                           |      |  122 ns |  126 ns |            |
| call of the plain function                                   | 2 clients                           |      | 41.0 ns | 45.9 ns |            |
| resolve(), cold                                              | N consumers of a Client             |   10 | 97.7 µs |  105 µs |    9.77 µs |
| resolve(), cold                                              | N consumers of a Client             |  100 |  883 µs | 1.05 ms |    8.83 µs |
| resolve(), cold                                              | N consumers of a Client             | 1000 | 9.82 ms | 10.6 ms |    9.82 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient |   10 |  149 µs |  167 µs |    14.9 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient |  100 | 1.45 ms | 1.85 ms |    14.5 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient | 1000 | 16.3 ms | 20.5 ms |    16.3 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              |   10 | 1.41 ms | 1.67 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              |   10 | 79.7 µs | 85.7 µs |    7.97 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              |  100 | 2.26 ms | 2.50 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              |  100 |  875 µs | 1.08 ms |    8.75 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              | 1000 | 10.4 ms | 11.1 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              | 1000 | 8.82 ms | 9.58 ms |    8.82 µs |
| one request                                                  | a client through nuke-di            |      |  104 µs |  109 µs |            |
| one request                                                  | a plain FastAPI Depends()           |      |  101 µs |  108 µs |            |
| one request                                                  | no dependencies                     |      | 97.7 µs |  102 µs |            |
| import, fresh interpreter                                    | nuke_di                             |      | 33.2 ms | 36.1 ms |            |
| import, fresh interpreter                                    | nuke_di.fastapi                     |      |  169 ms |  176 ms |            |
| import, fresh interpreter                                    | fastapi                             |      |  166 ms |  169 ms |            |
| resolve(), tracemalloc peak                                  | mixed                               | 1000 |  388 kB |  463 kB |      388 B |

### Python 3.13.14

nuke-di 1.8.0 · CPython 3.13.14 · macOS-26.6.2-arm64-arm-64bit-Mach-O · commit 11f5919 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                     | Shape                               |    N |  Median |     p95 | Per client |
|--------------------------------------------------------------|-------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                              | wide                                |   10 | 73.8 µs | 83.4 µs |    7.38 µs |
| resolve(), warm                                              | wide                                |   10 |  113 ns |  119 ns |            |
| resolve(), cold                                              | wide                                |  100 |  751 µs |  860 µs |    7.51 µs |
| resolve(), warm                                              | wide                                |  100 |  124 ns |  134 ns |            |
| resolve(), cold                                              | wide                                | 1000 | 8.34 ms | 9.13 ms |    8.34 µs |
| resolve(), warm                                              | wide                                | 1000 |  128 ns |  137 ns |            |
| resolve(), cold                                              | deep                                |   10 | 77.3 µs |  130 µs |    7.73 µs |
| resolve(), warm                                              | deep                                |   10 |  112 ns |  126 ns |            |
| resolve(), cold                                              | deep                                |  100 |  778 µs |  973 µs |    7.78 µs |
| resolve(), warm                                              | deep                                |  100 |  113 ns |  140 ns |            |
| resolve(), cold                                              | deep                                | 1000 | 11.3 ms | 12.2 ms |    11.3 µs |
| resolve(), warm                                              | deep                                | 1000 |  107 ns |  108 ns |            |
| resolve(), cold                                              | mixed                               |   10 | 90.1 µs |  119 µs |    9.01 µs |
| resolve(), warm                                              | mixed                               |   10 |  107 ns |  107 ns |            |
| resolve(), cold                                              | mixed                               |  100 |  884 µs | 1.13 ms |    8.84 µs |
| resolve(), warm                                              | mixed                               |  100 |  112 ns |  123 ns |            |
| resolve(), cold                                              | mixed                               | 1000 | 9.05 ms | 10.2 ms |    9.05 µs |
| resolve(), warm                                              | mixed                               | 1000 |  107 ns |  114 ns |            |
| connect() + disconnect()                                     | wide: N in one layer                |   10 |  251 µs |  338 µs |    25.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                |   10 | 1.96 µs | 3.48 µs |     196 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                |   10 |  248 µs |  336 µs |    24.8 µs |
| connect() + disconnect()                                     | wide: N in one layer                |  100 | 1.50 ms | 1.70 ms |    15.0 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                |  100 | 14.0 µs | 15.2 µs |     140 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                |  100 | 1.49 ms | 1.69 ms |    14.9 µs |
| connect() + disconnect()                                     | wide: N in one layer                | 1000 | 14.1 ms | 16.4 ms |    14.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                | 1000 |  174 µs |  347 µs |     174 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                | 1000 | 13.9 ms | 16.0 ms |    13.9 µs |
| connect() + disconnect()                                     | deep: N layers                      |   10 | 1.05 ms | 1.15 ms |     105 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      |   10 | 1.96 µs | 2.64 µs |     196 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      |   10 | 1.05 ms | 1.15 ms |     105 µs |
| connect() + disconnect()                                     | deep: N layers                      |  100 | 10.5 ms | 10.8 ms |     105 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      |  100 | 15.6 µs | 18.5 µs |     156 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      |  100 | 10.5 ms | 10.8 ms |     105 µs |
| connect() + disconnect()                                     | deep: N layers                      | 1000 |  106 ms |  108 ms |     106 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      | 1000 |  247 µs |  518 µs |     247 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      | 1000 |  105 ms |  108 ms |     105 µs |
| inject(), clients resolved                                   | 2 clients                           |      | 9.62 µs | 9.98 µs |            |
| call of the injected function                                | 2 clients                           |      |  104 ns |  107 ns |            |
| call of the plain function                                   | 2 clients                           |      | 37.2 ns | 38.2 ns |            |
| resolve(), cold                                              | N consumers of a Client             |   10 |  100 µs |  114 µs |    10.0 µs |
| resolve(), cold                                              | N consumers of a Client             |  100 |  929 µs |  993 µs |    9.29 µs |
| resolve(), cold                                              | N consumers of a Client             | 1000 | 10.1 ms | 11.4 ms |    10.1 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient |   10 |  151 µs |  157 µs |    15.1 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient |  100 | 1.54 ms | 1.78 ms |    15.4 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient | 1000 | 16.7 ms | 17.6 ms |    16.7 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              |   10 | 1.69 ms | 2.00 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              |   10 | 84.3 µs | 92.1 µs |    8.43 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              |  100 | 2.71 ms | 2.89 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              |  100 |  889 µs | 1.07 ms |    8.89 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              | 1000 | 11.3 ms | 12.3 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              | 1000 | 9.39 ms | 10.3 ms |    9.39 µs |
| one request                                                  | a client through nuke-di            |      |  107 µs |  109 µs |            |
| one request                                                  | a plain FastAPI Depends()           |      |  107 µs |  110 µs |            |
| one request                                                  | no dependencies                     |      | 99.3 µs |  102 µs |            |
| import, fresh interpreter                                    | nuke_di                             |      | 34.7 ms | 35.9 ms |            |
| import, fresh interpreter                                    | nuke_di.fastapi                     |      |  173 ms |  176 ms |            |
| import, fresh interpreter                                    | fastapi                             |      |  157 ms |  162 ms |            |
| resolve(), tracemalloc peak                                  | mixed                               | 1000 |  404 kB |  479 kB |      404 B |

### Python 3.14.6

nuke-di 1.8.0 · CPython 3.14.6 · macOS-26.6.2-arm64-arm-64bit-Mach-O · commit 11f5919 · N = 10, 100, 1000 · 20 repeats

| Scenario                                                     | Shape                               |    N |  Median |     p95 | Per client |
|--------------------------------------------------------------|-------------------------------------|-----:|--------:|--------:|-----------:|
| resolve(), cold                                              | wide                                |   10 | 70.5 µs | 75.0 µs |    7.05 µs |
| resolve(), warm                                              | wide                                |   10 |  123 ns |  138 ns |            |
| resolve(), cold                                              | wide                                |  100 |  737 µs |  853 µs |    7.37 µs |
| resolve(), warm                                              | wide                                |  100 |  117 ns |  123 ns |            |
| resolve(), cold                                              | wide                                | 1000 | 8.43 ms | 9.01 ms |    8.43 µs |
| resolve(), warm                                              | wide                                | 1000 |  118 ns |  119 ns |            |
| resolve(), cold                                              | deep                                |   10 | 69.6 µs | 72.9 µs |    6.96 µs |
| resolve(), warm                                              | deep                                |   10 |  118 ns |  130 ns |            |
| resolve(), cold                                              | deep                                |  100 |  755 µs |  987 µs |    7.55 µs |
| resolve(), warm                                              | deep                                |  100 |  117 ns |  128 ns |            |
| resolve(), cold                                              | deep                                | 1000 | 11.8 ms | 12.4 ms |    11.8 µs |
| resolve(), warm                                              | deep                                | 1000 |  122 ns |  143 ns |            |
| resolve(), cold                                              | mixed                               |   10 | 89.7 µs |  143 µs |    8.97 µs |
| resolve(), warm                                              | mixed                               |   10 |  118 ns |  119 ns |            |
| resolve(), cold                                              | mixed                               |  100 |  884 µs | 1.02 ms |    8.84 µs |
| resolve(), warm                                              | mixed                               |  100 |  118 ns |  126 ns |            |
| resolve(), cold                                              | mixed                               | 1000 | 9.17 ms | 9.86 ms |    9.17 µs |
| resolve(), warm                                              | mixed                               | 1000 |  116 ns |  140 ns |            |
| connect() + disconnect()                                     | wide: N in one layer                |   10 |  249 µs |  294 µs |    24.9 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                |   10 | 2.42 µs | 3.17 µs |     242 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                |   10 |  246 µs |  291 µs |    24.6 µs |
| connect() + disconnect()                                     | wide: N in one layer                |  100 | 1.31 ms | 1.57 ms |    13.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                |  100 | 18.0 µs | 19.4 µs |     180 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                |  100 | 1.29 ms | 1.55 ms |    12.9 µs |
| connect() + disconnect()                                     | wide: N in one layer                | 1000 | 12.1 ms | 12.8 ms |    12.1 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | wide: N in one layer                | 1000 |  182 µs |  274 µs |     182 ns |
| connect() + disconnect(), overhead above the ideal           | wide: N in one layer                | 1000 | 11.9 ms | 12.6 ms |    11.9 µs |
| connect() + disconnect()                                     | deep: N layers                      |   10 | 1.06 ms | 1.17 ms |     106 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      |   10 | 2.62 µs | 3.07 µs |     262 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      |   10 | 1.06 ms | 1.17 ms |     106 µs |
| connect() + disconnect()                                     | deep: N layers                      |  100 | 10.4 ms | 13.2 ms |     104 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      |  100 | 21.1 µs | 32.0 µs |     211 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      |  100 | 10.3 ms | 13.2 ms |     103 µs |
| connect() + disconnect()                                     | deep: N layers                      | 1000 |  103 ms |  107 ms |     103 µs |
| connect() + disconnect(), ideal: coroutines awaited directly | deep: N layers                      | 1000 |  289 µs |  381 µs |     289 ns |
| connect() + disconnect(), overhead above the ideal           | deep: N layers                      | 1000 |  103 ms |  107 ms |     103 µs |
| inject(), clients resolved                                   | 2 clients                           |      | 9.37 µs | 10.1 µs |            |
| call of the injected function                                | 2 clients                           |      |  113 ns |  116 ns |            |
| call of the plain function                                   | 2 clients                           |      | 36.8 ns | 37.6 ns |            |
| resolve(), cold                                              | N consumers of a Client             |   10 |  100 µs |  117 µs |    10.0 µs |
| resolve(), cold                                              | N consumers of a Client             |  100 |  925 µs | 1.06 ms |    9.25 µs |
| resolve(), cold                                              | N consumers of a Client             | 1000 | 10.3 ms | 11.1 ms |    10.3 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient |   10 |  151 µs |  162 µs |    15.1 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient |  100 | 1.51 ms | 1.74 ms |    15.1 µs |
| resolve(), cold                                              | N consumers of a NotSingletonClient | 1000 | 16.8 ms | 17.8 ms |    16.8 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              |   10 | 1.48 ms | 1.81 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              |   10 | 82.1 µs |  173 µs |    8.21 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              |  100 | 2.43 ms | 2.83 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              |  100 |  865 µs | 1.06 ms |    8.65 µs |
| flush() + mock() + resolve()                                 | mixed, a leaf replaced              | 1000 | 11.3 ms | 12.0 ms |            |
| override() block + resolve()                                 | mixed, a leaf replaced              | 1000 | 9.07 ms | 9.70 ms |    9.07 µs |
| one request                                                  | a client through nuke-di            |      |  112 µs |  120 µs |            |
| one request                                                  | a plain FastAPI Depends()           |      |  114 µs |  119 µs |            |
| one request                                                  | no dependencies                     |      |  106 µs |  109 µs |            |
| import, fresh interpreter                                    | nuke_di                             |      | 31.8 ms | 43.3 ms |            |
| import, fresh interpreter                                    | nuke_di.fastapi                     |      |  164 ms |  195 ms |            |
| import, fresh interpreter                                    | fastapi                             |      |  145 ms |  178 ms |            |
| resolve(), tracemalloc peak                                  | mixed                               | 1000 |  404 kB |  479 kB |      404 B |
