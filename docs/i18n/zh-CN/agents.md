# <a id="coding-agents"></a>编程智能体

[English](../../guide/agents.md) · [Русский](../ru/agents.md) · **简体中文** · [Español](../es/agents.md) · [Português (Brasil)](../pt-BR/agents.md) · [日本語](../ja/agents.md) · [Polski](../pl/agents.md)

← [文档](../README.zh-CN.md#documentation)

没有读过 `nuke-di` 文档的编程智能体（Claude Code、Codex、Cursor、Copilot、Gemini CLI）会按照其他 DI 库的样子来写它：
provider 函数、`bind(Protocol, Impl)`、请求作用域、在 `connect()` 中重试。这些在这里都不存在，而且是有意为之。
把下面任意一种材料交给智能体，它就会改为编写客户端。

| 内容                                  | 适用于                                                      |
|---------------------------------------|-------------------------------------------------------------|
| [Agent Skill](#the-agent-skill)       | Claude Code 以插件形式使用；Codex、Cursor、Copilot 和 Gemini CLI 以文件形式使用 |
| [AGENTS.md 片段](#a-block-for-agentsmd) | 任何会读取 `AGENTS.md` 或 `CLAUDE.md` 的智能体 |
| [llms.txt](#llmstxt)                  | 通过 URL 获取文档的智能体                                   |
| [Context7](#context7)                 | 装有 Context7 MCP 服务器的智能体                            |
| [JSON 格式的依赖图](#the-graph-as-json) | 要检查自己所写装配的智能体                                |

## <a id="the-agent-skill"></a>Agent Skill

[`skills/nuke-di/SKILL.md`](../../../skills/nuke-di/SKILL.md) 用一页讲清整个模型：什么是客户端、
常见任务的写法、一张"不要这样写"的表格（每一项都附带原因），以及如何检查结果。
当任务涉及 `nuke_di` 时，智能体会加载它。

它的效果是测出来的，而不是假设的：[benchmarks/agents](../../../benchmarks/agents/README.md) 让智能体在示例上完成编码任务，
分别带上和不带这个 skill，并用 mypy、pytest 以及对被否决设计的搜索来给每个结果打分。

在 Claude Code 中，这个仓库就是一个插件市场：

```text
/plugin marketplace add troyan-dy/nuke-di
/plugin install nuke-di@nuke-di
```

Codex、Cursor、GitHub Copilot 和 Gemini CLI 从项目的 `.agents/skills/` 读取 [Agent Skills](https://agentskills.io)
格式的 skill；不装插件的 Claude Code 读取 `.claude/skills/`：

```bash
mkdir -p .agents/skills/nuke-di
curl -fsSL https://raw.githubusercontent.com/troyan-dy/nuke-di/master/skills/nuke-di/SKILL.md \
  -o .agents/skills/nuke-di/SKILL.md
```

## <a id="a-block-for-agentsmd"></a>AGENTS.md 片段

不支持 skill 的智能体仍然会读取项目的说明文件。把下面的内容粘贴到它的
`AGENTS.md`（Codex、Cursor、Copilot）或 `CLAUDE.md`（Claude Code）中：

```markdown
## Dependency injection: nuke-di

This project uses nuke-di. A dependency is a `nuke_di.Client` subclass whose `__init__` takes its own
dependencies as type-hinted arguments; I/O goes in `async def connect()` / `disconnect()`, never in `__init__`.
Functions, `@job` / `@worker` entrypoints, FastAPI / Litestar / FastStream handlers and MCP tools take clients by type hint.

Do not write what nuke-di rejects:
- provider or factory functions: wrap a third-party object in a Client that creates it in connect();
- `bind(Protocol, Impl)`, qualifiers, multibinding: depend on the concrete client, swap it in tests;
- per-request or per-message clients, new code on `NotSingletonClient`: open per-request state through a method;
- retries or waiting loops in `connect()`: let it raise, the orchestrator restarts the process.

Tests replace clients with `DI.mock()` / `DI.override()` before anything is resolved. Check the wiring with
mypy and `plugins = ["nuke_di.mypy"]`, and with a test that calls `Dependencies().inject(entrypoint)`.
Manual: https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt
```

## <a id="llmstxt"></a>llms.txt

仓库根目录下有两个文件，由英文页面生成：

| 文件 | 内容 |
|------|------|
| [`llms.txt`](../../../llms.txt) | [llms.txt](https://llmstxt.org) 格式的索引：`nuke-di` 是什么、不做什么，以及指向每个页面的链接 |
| [`llms-full.txt`](../../../llms-full.txt) | README 和整个指南合成一个文件，约 26k token |

把原始文件的 URL `https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt` 交给智能体，
它一次请求就能读完整份手册。

## <a id="context7"></a>Context7

[Context7](https://context7.com) 通过它的 MCP 服务器把库的文档提供给智能体。
仓库根目录下的 `context7.json` 告诉它要索引哪些内容：README、指南和示例，不包括翻译。
安装该服务器后，按名字请求即可："use context7 for nuke-di"。

## <a id="the-graph-as-json"></a>JSON 格式的依赖图

智能体可以把容器的[依赖图](clients.md#the-graph)作为数据，来检查自己写的装配：
一个入口点会构建哪些客户端，每个客户端又依赖什么。库里没有 JSON 导出，
因为 `Graph.nodes` 几行代码就能给出它：

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

节点按解析顺序排列，因此客户端排在它的依赖之后，连接时也在它们之后。
来自 `mock()` 或 `override()` 的 Replacement 保留它所代替的类，对象的类型写在 `replacement` 中；它永远不会连接。
对于一个应用，按入口点的方式构建容器（对 job 来说是 `deps.inject(sync)`），然后打印 JSON。

[mypy 插件](clients.md#checking-the-tree-with-mypy)和
[注入每个入口点的测试](testing.md)无需阅读装配就能检查同样的内容。
