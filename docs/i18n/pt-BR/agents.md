# <a id="coding-agents"></a>Agentes de código

[English](../../guide/agents.md) · [Русский](../ru/agents.md) · [简体中文](../zh-CN/agents.md) · [Español](../es/agents.md) · **Português (Brasil)** · [日本語](../ja/agents.md) · [Polski](../pl/agents.md)

← [Documentação](../README.pt-BR.md#documentation)

Um agente de código (Claude Code, Codex, Cursor, Copilot, Gemini CLI) que não leu nada sobre o `nuke-di`
o escreve no formato de outras bibliotecas de DI: funções provedoras, `bind(Protocol, Impl)`, escopos
por requisição, retentativas em `connect()`. Nada disso existe aqui, de propósito. Dê ao agente uma das
páginas abaixo, e ele passa a escrever clientes.

| O quê                                 | Para                                                        |
|---------------------------------------|-------------------------------------------------------------|
| [A Agent Skill](#the-agent-skill)     | Claude Code como plugin; Codex, Cursor, Copilot e Gemini CLI como arquivo |
| [Um bloco para o AGENTS.md](#a-block-for-agentsmd) | Qualquer agente que lê `AGENTS.md` ou `CLAUDE.md` |
| [llms.txt](#llmstxt)                  | Um agente que busca a documentação por URL                  |
| [Context7](#context7)                 | Um agente com o servidor MCP do Context7                    |
| [O grafo como JSON](#the-graph-as-json) | Um agente que verifica a fiação que escreveu              |

## <a id="the-agent-skill"></a>A Agent Skill

[`skills/nuke-di/SKILL.md`](../../../skills/nuke-di/SKILL.md) é o modelo em uma página: o que é um
cliente, as receitas para as tarefas comuns, uma tabela do que não escrever com o motivo de cada item, e
como verificar o resultado. O agente a carrega quando uma tarefa envolve o `nuke_di`.

Isso é medido, não suposto: [benchmarks/agents](../../../benchmarks/agents/README.md) dá a um agente
tarefas de programação sobre os exemplos, com a skill e sem ela, e avalia cada resultado com mypy,
pytest e uma busca pelos designs rejeitados.

No Claude Code, o repositório é um marketplace de plugins:

```text
/plugin marketplace add troyan-dy/nuke-di
/plugin install nuke-di@nuke-di
```

Codex, Cursor, GitHub Copilot e Gemini CLI leem skills no formato [Agent Skills](https://agentskills.io)
a partir de `.agents/skills/` do projeto; o Claude Code sem o plugin lê `.claude/skills/`:

```bash
mkdir -p .agents/skills/nuke-di
curl -fsSL https://raw.githubusercontent.com/troyan-dy/nuke-di/master/skills/nuke-di/SKILL.md \
  -o .agents/skills/nuke-di/SKILL.md
```

## <a id="a-block-for-agentsmd"></a>Um bloco para o AGENTS.md

Um agente sem skills ainda lê as instruções do projeto. Cole isto no `AGENTS.md` dele
(Codex, Cursor, Copilot) ou no `CLAUDE.md` (Claude Code):

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

Dois arquivos na raiz do repositório, gerados a partir das páginas em inglês:

| Arquivo | Conteúdo |
|---------|----------|
| [`llms.txt`](../../../llms.txt) | Um índice no formato [llms.txt](https://llmstxt.org): o que o `nuke-di` é e o que não faz, um link para cada página |
| [`llms-full.txt`](../../../llms-full.txt) | O README e o guia inteiro em um só arquivo, cerca de 26 mil tokens |

Dê a um agente a URL raw, `https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt`,
e ele lê o manual inteiro com uma única requisição.

## <a id="context7"></a>Context7

O [Context7](https://context7.com) entrega a documentação de uma biblioteca a um agente pelo seu servidor
MCP. O `context7.json` na raiz do repositório diz a ele o que indexar: o README, o guia e os exemplos,
sem as traduções. Com o servidor instalado, peça pelo nome:
"use context7 for nuke-di".

## <a id="the-graph-as-json"></a>O grafo como JSON

Um agente verifica a fiação que escreveu a partir do [grafo](clients.md#the-graph) do container, como
dados: quais clientes um entrypoint constrói e do que cada um depende. Não há exportação para JSON na
biblioteca, já que `Graph.nodes` a fornece em poucas linhas:

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

Os nós vêm na ordem de resolução, então um cliente vem depois das suas dependências, e também se conecta
depois delas. Um substituto de `mock()` ou `override()` mantém a classe que ele substitui, com o tipo do
objeto em `replacement`; ele nunca é conectado. Para uma aplicação, construa o container do mesmo jeito
que o entrypoint faz, `deps.inject(sync)` para um job, e então imprima o JSON.

O [plugin do mypy](clients.md#checking-the-tree-with-mypy) e
[um teste que injeta todos os entrypoints](testing.md) verificam a mesma fiação sem precisar lê-la.
