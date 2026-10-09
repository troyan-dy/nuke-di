# Agent evals

Does [the Agent Skill](../../skills/nuke-di/SKILL.md) make a coding agent write better `nuke-di` code? Each task
below goes to Claude Code in print mode, in a fresh project, once per condition; then the project is graded by
commands that need no judgment.

```console
$ uv run python benchmarks/agents/evaluate.py --model sonnet --conditions baseline skill agents-md --repeats 3 --jobs 6
```

Every run is a paid agent session (about $0.30–0.60 with Sonnet), so this is not part of the test suite.
`--model reference` grades the known-good solutions of [`reference/`](reference/) instead, with no agent: every
task must pass, or a grade is wrong. `--regrade` grades the projects of an earlier run again, after a grader changed.

## Conditions

| Condition   | The agent has                                                                                   |
|-------------|-------------------------------------------------------------------------------------------------|
| `baseline`  | `nuke-di` installed in the environment, nothing else; it can read the installed source          |
| `skill`     | the same, plus this repository as a Claude Code plugin (`--plugin-dir`), i.e. `skills/nuke-di/SKILL.md` |
| `agents-md` | the same as baseline, plus the block of [docs/guide/agents.md](../../docs/guide/agents.md#a-block-for-agentsmd) as the project's `CLAUDE.md` |

The agent runs with `--setting-sources project --strict-mcp-config`: no user settings, plugins, hooks or MCP
servers, and no web access.

## Tasks

| Task            | Starts from               | Asks for                                                                 | The trap |
|-----------------|---------------------------|--------------------------------------------------------------------------|----------|
| `fresh_fastapi` | an empty project          | a FastAPI app over an in-memory store and an HTTP payments service      | an `httpx.AsyncClient` created in `__init__` or a provider function |
| `fresh_job`     | an empty project          | a job with `--day`, Redis connecting only after Postgres                | ordering by hand instead of by a dependency |
| `fresh_worker`  | an empty project          | a consumer that finishes its message on SIGTERM                         | signal handling by hand |
| `protocol`      | `examples/fastapi_app`    | `UserCache` depending on a `UserSource` protocol                        | a `Protocol` argument, which the container cannot fill (ADR-0008) |
| `startup`       | `examples/fastapi_app`    | a startup that survives a database unreachable for a few seconds        | a retry loop in `connect()` (ADR-0009) |
| `audit`         | `examples/fastapi_app`    | a per-request id recorded in an audit log                               | a per-request client (ADR-0006) |

## Grades

A run passes when all four do:

| Grade         | Passes when |
|---------------|-------------|
| `agent tests` | `pytest` over the project, the agent's own tests included, passes |
| `hidden`      | the task's tests of [`hidden/`](hidden/), copied in after the agent finished, pass |
| `mypy`        | mypy with the `nuke_di.mypy` plugin runs and reports no `[nuke-di]` error |
| `shapes`      | no rejected design is found in the code: `NotSingletonClient`, `bind()` or `bind` / `provide` / `register` on a container, a module-level function that returns a client or a connection (a provider), a loop in `connect()` that retries, catches or sleeps, a loop anywhere that catches and sleeps, an `AsyncClient` / `ClientSession` / pool created in `__init__`, `tenacity` or `backoff` |

## Results

2026-10-09, Claude Code 2.1.283, `--model sonnet`, 6 tasks × 3 runs per condition, on the code of 1.12.1
([raw results](results/2026-10-09-sonnet.json)):

| Task | baseline | skill | agents-md |
|---|---:|---:|---:|
| fresh_fastapi | 1/3 | 3/3 | 3/3 |
| fresh_job | 3/3 | 3/3 | 3/3 |
| fresh_worker | 3/3 | 3/3 | 3/3 |
| protocol | 2/3 | 3/3 | 3/3 |
| startup | 0/3 | 3/3 | 3/3 |
| audit | 3/3 | 3/3 | 3/3 |
| **all** | **12/18** | **18/18** | **18/18** |

| Per run, on average | baseline | skill | agents-md |
|---------------------|---------:|------:|----------:|
| Cost                | $0.61    | $0.31 | $0.59     |
| Turns               | 24       | 15    | 25        |
| Wall time           | 4.1 min  | 1.7 min | 3.6 min |
| Loaded the skill    | —        | 18/18 | —         |

What the baseline got wrong:

- `startup`, 3 of 3: a `while` / `for` loop with `try` and `asyncio.sleep` inside `Database.connect()`, the retry
  that ADR-0009 rejects.
- `fresh_fastapi`, 2 of 3: the `httpx.AsyncClient` created in `__init__` of the client, so building the tree opens
  a connection pool before anything connects.
- `protocol`, 1 of 3: `UserCache.__init__` took the `UserSource` protocol, and the application no longer started
  (`InvalidSignatureError: ... which is not a client`).

Both the skill and the `AGENTS.md` block remove all three. The skill also halves the cost, the turns and the time
of a run: the agent no longer reads the installed source to learn the API. The tasks a baseline passes
(`fresh_job`, `fresh_worker`, `audit`) are those where reading the source, or the example next to the task, is
enough.

Limits: one model, three runs per cell, and graders that look for known shapes; a design the graders do not know
passes them. After the run, two sentences of `SKILL.md` were corrected for accuracy (a class order in the first
example and the explanation of `which is not a client`); the recipes and the table of rejected designs are as
measured.
