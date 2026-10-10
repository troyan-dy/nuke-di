# <a id="coding-agents"></a>Agentes de programación

[English](../../guide/agents.md) · [Русский](../ru/agents.md) · [简体中文](../zh-CN/agents.md) · **Español** · [Português (Brasil)](../pt-BR/agents.md) · [日本語](../ja/agents.md) · [Polski](../pl/agents.md)

← [Documentación](../README.es.md#documentation)

Un agente de programación (Claude Code, Codex, Cursor, Copilot, Gemini CLI) que no ha leído sobre
`nuke-di` lo escribe con la forma de otras bibliotecas de DI: funciones proveedoras,
`bind(Protocol, Impl)`, scopes por petición, reintentos en `connect()`. Nada de eso existe aquí, a
propósito. Dale al agente una de las páginas de abajo y escribirá clientes en su lugar.

| Qué                                   | Para                                                        |
|---------------------------------------|-------------------------------------------------------------|
| [La Agent Skill](#the-agent-skill)    | Claude Code como plugin; Codex, Cursor, Copilot y Gemini CLI como archivo |
| [Un bloque para AGENTS.md](#a-block-for-agentsmd) | Cualquier agente que lea `AGENTS.md` o `CLAUDE.md` |
| [llms.txt](#llmstxt)                  | Un agente que obtiene la documentación por URL              |
| [Context7](#context7)                 | Un agente con el servidor MCP de Context7                   |
| [El grafo como JSON](#the-graph-as-json) | Un agente que comprueba el cableado que escribió         |

## <a id="the-agent-skill"></a>La Agent Skill

[`skills/nuke-di/SKILL.md`](../../../skills/nuke-di/SKILL.md) es el modelo en una sola página: qué es
un cliente, las recetas para las tareas habituales, una tabla de lo que no hay que escribir con el
motivo de cada cosa, y cómo comprobar el resultado. El agente la carga cuando una tarea toca
`nuke_di`.

Está medida, no supuesta: [benchmarks/agents](../../../benchmarks/agents/README.md) le da a un agente
tareas de programación sobre los ejemplos, con la skill y sin ella, y califica cada resultado con mypy,
pytest y una búsqueda de los diseños rechazados.

En Claude Code, el repositorio es un marketplace de plugins:

```text
/plugin marketplace add troyan-dy/nuke-di
/plugin install nuke-di@nuke-di
```

Codex, Cursor, GitHub Copilot y Gemini CLI leen skills en el formato [Agent Skills](https://agentskills.io)
desde `.agents/skills/` del proyecto; Claude Code sin el plugin lee `.claude/skills/`:

```bash
mkdir -p .agents/skills/nuke-di
curl -fsSL https://raw.githubusercontent.com/troyan-dy/nuke-di/master/skills/nuke-di/SKILL.md \
  -o .agents/skills/nuke-di/SKILL.md
```

## <a id="a-block-for-agentsmd"></a>Un bloque para AGENTS.md

Un agente sin skills sigue leyendo las instrucciones del proyecto. Pega esto en su `AGENTS.md`
(Codex, Cursor, Copilot) o `CLAUDE.md` (Claude Code):

```markdown
## Dependency injection: nuke-di

This project uses nuke-di. A dependency is a `nuke_di.Client` subclass whose `__init__` takes its own
dependencies as type-hinted arguments; I/O goes in `async def connect()` / `disconnect()`, never in `__init__`.
Functions, `@job` / `@worker` entrypoints, FastAPI / Litestar / FastStream / aiogram handlers, MCP tools and taskiq tasks take clients by type hint.

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

Dos archivos en la raíz del repositorio, generados a partir de las páginas en inglés:

| Archivo | Contenido |
|---------|-----------|
| [`llms.txt`](../../../llms.txt) | Un índice en el formato [llms.txt](https://llmstxt.org): qué es `nuke-di` y qué no hace, un enlace a cada página |
| [`llms-full.txt`](../../../llms-full.txt) | El README y la guía completa en un solo archivo, unos 35k tokens |

Dale a un agente la URL raw, `https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt`,
y leerá el manual completo con una sola petición.

## <a id="context7"></a>Context7

[Context7](https://context7.com) sirve la documentación de una biblioteca a un agente a través de su
servidor MCP. `context7.json` en la raíz del repositorio le indica qué indexar: el README, la guía y
los ejemplos, sin las traducciones. Con el servidor instalado, pídela por su nombre:
"use context7 for nuke-di".

## <a id="the-graph-as-json"></a>El grafo como JSON

Un agente comprueba el cableado que escribió a partir del [grafo](clients.md#the-graph) del
contenedor, como datos: qué clientes construye un entrypoint y de qué depende cada uno. La biblioteca
no tiene exportación a JSON, ya que `Graph.nodes` la da en unas pocas líneas:

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

Los nodos llegan en orden de resolución, así que un cliente va después de sus dependencias, y también
se conecta después de ellas. Un Reemplazo de `mock()` u `override()` conserva la clase a la que
sustituye, con el tipo del objeto en `replacement`; nunca se conecta. Para una aplicación, construye
el contenedor como lo hace el entrypoint, `deps.inject(sync)` para un job, y luego imprime el JSON.

El [plugin de mypy](clients.md#checking-the-tree-with-mypy) y
[una prueba que inyecta cada entrypoint](testing.md) comprueban el mismo cableado sin leerlo.
