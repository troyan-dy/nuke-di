# Coding agents

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

## The Agent Skill

[`skills/nuke-di/SKILL.md`](../../skills/nuke-di/SKILL.md) is the model on one page: what a client
is, the recipes for the common tasks, a table of what not to write with the reason for each, and
how to check the result. The agent loads it when a task touches `nuke_di`.

In the [2026-10-09 evaluation](https://github.com/troyan-dy/nuke-di/blob/26e13c658131e13871f0a1470761f998b424d1bb/benchmarks/agents/README.md)
of nuke-di 1.12.1, Claude Code with Sonnet passed 12 of 18 runs without guidance and 18 of 18
with the skill. The evaluation covered the 1.x API.

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
Functions, `@entrypoint` functions, FastAPI / Litestar / FastStream / aiogram handlers, MCP tools and taskiq tasks take clients by type hint;
a Starlette or Quart app lists them in `clients = nuke_di.asgi.lifespan(DI, ...)` and calls `clients.get(...)`.
Build new clients and bind functions before connecting. `resolve()` can look up a client the
container already holds while connected. Create fresh client bindings after a flush or disconnect.

Do not write what nuke-di rejects:
- provider or factory functions: wrap a third-party object in a Client that creates it in connect();
- `bind(Protocol, Impl)`, qualifiers, multibinding: depend on the concrete client, swap it in tests;
- per-request or per-message clients: open per-request state through a method;
- retries or waiting loops in `connect()`: let it raise, the orchestrator restarts the process.
- process options, signals and task supervision in DI: keep them in the application or framework.

Tests replace clients with `DI.mock()` / `DI.override()` before anything is resolved. Check the wiring with
mypy and `plugins = ["nuke_di.mypy"]`, and with a test that calls `Dependencies().inject(entrypoint)`.
Manual: https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt
```

## llms.txt

Two files at the root of the repository, generated from the English pages:

| File | Contents |
|------|----------|
| [`llms.txt`](../../llms.txt) | An index in the [llms.txt](https://llmstxt.org) format: what `nuke-di` is and does not do, a link to every page |
| [`llms-full.txt`](../../llms-full.txt) | The README and the whole guide in one file |

Give an agent the raw URL, `https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt`,
and it reads the whole manual with one fetch.

## Context7

[Context7](https://context7.com) serves the documentation of a library to an agent through its MCP
server. `context7.json` at the root of the repository tells it what to index: the README, the guide
and the examples. With the server installed, ask for it by name:
"use context7 for nuke-di".
