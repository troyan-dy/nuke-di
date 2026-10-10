# <a id="coding-agents"></a>Agenty programistyczne

[English](../../guide/agents.md) · [Русский](../ru/agents.md) · [简体中文](../zh-CN/agents.md) · [Español](../es/agents.md) · [Português (Brasil)](../pt-BR/agents.md) · [日本語](../ja/agents.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

Agent programistyczny (Claude Code, Codex, Cursor, Copilot, Gemini CLI), który nie czytał o `nuke-di`,
pisze kod na wzór innych bibliotek DI: funkcje-providery, `bind(Protocol, Impl)`, zasięgi na poziomie
żądania, ponawianie prób w `connect()`. Niczego z tego tu nie ma, celowo. Daj agentowi jedną ze stron poniżej,
a zamiast tego napisze klientów.

| Co                                    | Dla                                                         |
|---------------------------------------|-------------------------------------------------------------|
| [Agent Skill](#the-agent-skill)       | Claude Code jako plugin; Codex, Cursor, Copilot i Gemini CLI jako plik |
| [Blok dla AGENTS.md](#a-block-for-agentsmd) | Każdy agent, który czyta `AGENTS.md` lub `CLAUDE.md` |
| [llms.txt](#llmstxt)                  | Agent, który pobiera dokumentację z URL-a                   |
| [Context7](#context7)                 | Agent z serwerem MCP Context7                               |
| [Graf jako JSON](#the-graph-as-json)  | Agent, który sprawdza napisane przez siebie okablowanie     |

## <a id="the-agent-skill"></a>Agent Skill

[`skills/nuke-di/SKILL.md`](../../../skills/nuke-di/SKILL.md) to cały model na jednej stronie: czym jest
klient, przepisy na typowe zadania, tabela tego, czego nie pisać, z powodem przy każdej pozycji, i sposób
sprawdzenia wyniku. Agent ładuje go, gdy zadanie dotyczy `nuke_di`.

To zmierzone, a nie założone: [benchmarks/agents](../../../benchmarks/agents/README.md) daje agentowi
zadania programistyczne na przykładach, ze skillem i bez niego, i ocenia każdy wynik przez mypy,
pytest i wyszukiwanie odrzuconych rozwiązań.

W Claude Code repozytorium jest marketplace'em pluginów:

```text
/plugin marketplace add troyan-dy/nuke-di
/plugin install nuke-di@nuke-di
```

Codex, Cursor, GitHub Copilot i Gemini CLI czytają skille w formacie [Agent Skills](https://agentskills.io)
z katalogu `.agents/skills/` projektu; Claude Code bez pluginu czyta `.claude/skills/`:

```bash
mkdir -p .agents/skills/nuke-di
curl -fsSL https://raw.githubusercontent.com/troyan-dy/nuke-di/master/skills/nuke-di/SKILL.md \
  -o .agents/skills/nuke-di/SKILL.md
```

## <a id="a-block-for-agentsmd"></a>Blok dla AGENTS.md

Agent bez skilli i tak czyta instrukcje projektu. Wklej to do jego
`AGENTS.md` (Codex, Cursor, Copilot) lub `CLAUDE.md` (Claude Code):

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

Dwa pliki w katalogu głównym repozytorium, generowane z angielskich stron:

| Plik | Zawartość |
|------|-----------|
| [`llms.txt`](../../../llms.txt) | Indeks w formacie [llms.txt](https://llmstxt.org): czym `nuke-di` jest i czego nie robi, link do każdej strony |
| [`llms-full.txt`](../../../llms-full.txt) | README i cały przewodnik w jednym pliku, około 26 tys. tokenów |

Daj agentowi surowy URL, `https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt`,
a przeczyta cały podręcznik jednym pobraniem.

## <a id="context7"></a>Context7

[Context7](https://context7.com) udostępnia agentowi dokumentację biblioteki przez swój serwer
MCP. `context7.json` w katalogu głównym repozytorium mówi mu, co indeksować: README, przewodnik
i przykłady, bez tłumaczeń. Z zainstalowanym serwerem poproś o niego z nazwy:
„use context7 for nuke-di”.

## <a id="the-graph-as-json"></a>Graf jako JSON

Agent sprawdza napisane przez siebie okablowanie na podstawie [grafu](clients.md#the-graph) kontenera,
jako danych: jakich klientów buduje punkt wejścia i od czego zależy każdy z nich. Biblioteka nie ma
eksportu do JSON, bo `Graph.nodes` daje go w kilku linijkach:

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

Węzły przychodzą w kolejności rozwiązywania, więc klient jest po swoich zależnościach i po nich też
się łączy. Replacement z `mock()` lub `override()` zachowuje klasę, którą zastępuje, z typem obiektu
w `replacement`; nigdy nie jest łączony. W przypadku aplikacji zbuduj kontener tak, jak robi to punkt
wejścia, `deps.inject(sync)` dla joba, a potem wypisz JSON.

[Wtyczka mypy](clients.md#checking-the-tree-with-mypy) i
[test, który wstrzykuje każdy punkt wejścia](testing.md), sprawdzają to samo okablowanie bez czytania go.
