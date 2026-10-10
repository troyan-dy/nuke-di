# Coding agents

**English** · [Русский](../i18n/ru/agents.md) · [简体中文](../i18n/zh-CN/agents.md) · [Español](../i18n/es/agents.md) · [Português (Brasil)](../i18n/pt-BR/agents.md) · [日本語](../i18n/ja/agents.md) · [Polski](../i18n/pl/agents.md)

← [Documentation](../../README.md#documentation)

A coding agent (Claude Code, Codex, Cursor, Copilot, Gemini CLI) that has not read about `nuke-di`
writes it in the shape of other DI libraries: provider functions, `bind(Protocol, Impl)`, request
scopes, retries in `connect()`. None of them exist here, on purpose. Give the agent one of the
pages below, and it writes clients instead.

| What                                  | For                                                         |
|---------------------------------------|-------------------------------------------------------------|
| [The Agent Skill](#the-agent-skill)   | Claude Code as a plugin; Codex, Cursor, Copilot and Gemini CLI as a file |
| [A block for AGENTS.md](#a-block-for-agentsmd) | Any agent that reads `AGENTS.md` or `CLAUDE.md` |
| [llms.txt](#llmstxt)                  | An agent that fetches documentation by URL                  |
| [Context7](#context7)                 | An agent with the Context7 MCP server                       |
| [The graph as JSON](#the-graph-as-json) | An agent that checks the wiring it wrote                  |

## The Agent Skill

[`skills/nuke-di/SKILL.md`](../../skills/nuke-di/SKILL.md) is the model on one page: what a client
is, the recipes for the common tasks, a table of what not to write with the reason for each, and
how to check the result. The agent loads it when a task touches `nuke_di`.

It is measured, not assumed: [benchmarks/agents](../../benchmarks/agents/README.md) gives an agent
coding tasks on the examples, with the skill and without it, and grades each result with mypy,
pytest and a search for the rejected designs.

In Claude Code, the repository is a plugin marketplace:

```text
/plugin marketplace add troyan-dy/nuke-di
/plugin install nuke-di@nuke-di
```

Codex, Cursor, GitHub Copilot and Gemini CLI read skills in the [Agent Skills](https://agentskills.io)
format from `.agents/skills/` of the project; Claude Code without the plugin reads `.claude/skills/`:

```bash
mkdir -p .agents/skills/nuke-di
curl -fsSL https://raw.githubusercontent.com/troyan-dy/nuke-di/master/skills/nuke-di/SKILL.md \
  -o .agents/skills/nuke-di/SKILL.md
```

## A block for AGENTS.md

An agent without skills still reads the instructions of the project. Paste this into its
`AGENTS.md` (Codex, Cursor, Copilot) or `CLAUDE.md` (Claude Code):

```markdown
## Dependency injection: nuke-di

This project uses nuke-di. A dependency is a `nuke_di.Client` subclass whose `__init__` takes its own
dependencies as type-hinted arguments; I/O goes in `async def connect()` / `disconnect()`, never in `__init__`.
Functions, `@job` / `@worker` entrypoints and FastAPI / Litestar / FastStream / aiogram handlers take clients by type hint.

Do not write what nuke-di rejects:
- provider or factory functions: wrap a third-party object in a Client that creates it in connect();
- `bind(Protocol, Impl)`, qualifiers, multibinding: depend on the concrete client, swap it in tests;
- per-request or per-message clients, new code on `NotSingletonClient`: open per-request state through a method;
- retries or waiting loops in `connect()`: let it raise, the orchestrator restarts the process.

Tests replace clients with `DI.mock()` / `DI.override()` before anything is resolved. Check the wiring with
mypy and `plugins = ["nuke_di.mypy"]`, and with a test that calls `Dependencies().inject(entrypoint)`.
Manual: https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt
```

## llms.txt

Two files at the root of the repository, generated from the English pages:

| File | Contents |
|------|----------|
| [`llms.txt`](../../llms.txt) | An index in the [llms.txt](https://llmstxt.org) format: what `nuke-di` is and does not do, a link to every page |
| [`llms-full.txt`](../../llms-full.txt) | The README and the whole guide in one file, about 28k tokens |

Give an agent the raw URL, `https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt`,
and it reads the whole manual with one fetch.

## Context7

[Context7](https://context7.com) serves the documentation of a library to an agent through its MCP
server. `context7.json` at the root of the repository tells it what to index: the README, the guide
and the examples, without the translations. With the server installed, ask for it by name:
"use context7 for nuke-di".

## The graph as JSON

An agent checks the wiring it wrote from the [graph](clients.md#the-graph) of the container, as data:
which clients an entrypoint builds and what each one depends on. There is no JSON export in the
library, since `Graph.nodes` gives it in a few lines:

```python
# graph_json.py
import json

from nuke_di import Client, Dependencies, Node


class Postgres(Client):
    pass


class Redis(Client):
    pass


class Payments(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Checkout(Client):
    def __init__(self, pg: Postgres, redis: Redis, payments: Payments) -> None:
        self.pg, self.redis, self.payments = pg, redis, payments


def qualified(node: Node) -> str:
    return f"{node.cls.__module__}.{node.cls.__qualname__}"


def graph_json(deps: Dependencies) -> str:
    return json.dumps(
        [
            {
                "class": qualified(node),
                "dependencies": {argument: qualified(client) for argument, client in node.dependencies.items()},
                "replacement": None if node.replacement is None else type(node.replacement).__name__,
            }
            for node in deps.graph().nodes
        ],
        indent=2,
    )


deps = Dependencies()
deps.mock(Redis)
deps.resolve(Checkout)
print(graph_json(deps))
```

```console
$ python graph_json.py > graph.json
$ cat graph.json
[
  {
    "class": "__main__.Redis",
    "dependencies": {},
    "replacement": "NonCallableMagicMock"
  },
  {
    "class": "__main__.Postgres",
    "dependencies": {},
    "replacement": null
  },
  {
    "class": "__main__.Payments",
    "dependencies": {
      "pg": "__main__.Postgres"
    },
    "replacement": null
  },
  {
    "class": "__main__.Checkout",
    "dependencies": {
      "pg": "__main__.Postgres",
      "redis": "__main__.Redis",
      "payments": "__main__.Payments"
    },
    "replacement": null
  }
]
$ jq -r '.[] | select(.class == "__main__.Checkout") | .dependencies[]' graph.json
__main__.Postgres
__main__.Redis
__main__.Payments
```

The nodes come in resolution order, so a client comes after its dependencies, and it connects after
them too. A Replacement from `mock()` or `override()` keeps the class it stands in for, with the type
of the object in `replacement`; it is never connected. For an application, build the container the
way the entrypoint does, `deps.inject(sync)` for a job, then print the JSON.

The [mypy plugin](clients.md#checking-the-tree-with-mypy) and
[a test that injects every entrypoint](testing.md) check the same wiring without reading it.
