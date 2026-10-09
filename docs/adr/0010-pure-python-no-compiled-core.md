# Pure Python, no compiled core

nuke-di is pure Python with no runtime dependencies. A Rust (PyO3) core and compiled resolution were both measured and rejected: the library's own code has no CPU-bound stage to speed up. In the 2026-10-09 research round (#89), of 7.8 µs per resolved client 7.3 µs was the stdlib `inspect` / `typing` machinery, which stays interpreted Python whoever calls it, 0.3 µs the user's `__init__` and about 0.2 µs bookkeeping; mypyc on a copy of `core.py` gave +1% and broke the dataclass `field(init=False)` attributes. A per-class cache of the signatures (#29) took away most of the introspection cost in pure Python. A native core would cost a wheel matrix (Python 3.11–3.14 and free-threaded, three operating systems, two architectures), maturin in CI and the loss of "zero dependencies, pure Python", which the first research round (#16) named one of the library's two core strengths. Startup time is dominated by the clients' own `connect()`, where only scheduling helps (#28). Revisit only if a CPU-bound stage appears in the library itself.

## Considered Options

- **A Rust signature reader and graph through PyO3**: saves at most 0.5 µs per class once per process after the cache, string annotations still need `eval` in Python, graph work is under 0.1 ms at 1000 clients, and a connect scheduler would still create asyncio tasks.
- **mypyc** on the core module: +1%, with a dataclass incompatibility to work around.
- **Compiled resolution** as in wireup and dishka, code generated per tree: `resolve()` costs microseconds per client against milliseconds of `connect()`; see `docs/benchmarks.md` (#25).
