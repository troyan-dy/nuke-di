# <a id="coding-agents"></a>Агенты для написания кода

[English](../../guide/agents.md) · **Русский** · [简体中文](../zh-CN/agents.md) · [Español](../es/agents.md) · [Português (Brasil)](../pt-BR/agents.md) · [日本語](../ja/agents.md) · [Polski](../pl/agents.md)

← [Документация](../README.ru.md#documentation)

Агент для написания кода (Claude Code, Codex, Cursor, Copilot, Gemini CLI), который ничего не читал
про `nuke-di`, пишет код по образцу других DI-библиотек: функции-провайдеры, `bind(Protocol, Impl)`,
скоупы на запрос, повторные попытки в `connect()`. Ничего из этого здесь нет, и это сделано намеренно.
Дайте агенту один из разделов ниже, и он будет писать клиентов.

| Что                                   | Для кого                                                    |
|---------------------------------------|-------------------------------------------------------------|
| [Agent Skill](#the-agent-skill)       | Claude Code — как плагин; Codex, Cursor, Copilot и Gemini CLI — как файл |
| [Блок для AGENTS.md](#a-block-for-agentsmd) | Любой агент, который читает `AGENTS.md` или `CLAUDE.md` |
| [llms.txt](#llmstxt)                  | Агент, который загружает документацию по URL                |
| [Context7](#context7)                 | Агент с MCP-сервером Context7                               |
| [Граф в JSON](#the-graph-as-json)     | Агент, который проверяет написанную им проводку             |

## <a id="the-agent-skill"></a>Agent Skill

[`skills/nuke-di/SKILL.md`](../../../skills/nuke-di/SKILL.md) — вся модель на одной странице: что такое
клиент, рецепты для типовых задач, таблица того, чего писать не нужно, с причиной для каждого пункта,
и способ проверить результат. Агент загружает его, когда задача касается `nuke_di`.

Польза скилла измерена, а не предполагается: [benchmarks/agents](../../../benchmarks/agents/README.md)
даёт агенту задачи на примерах — со скиллом и без него — и оценивает каждый результат с помощью mypy,
pytest и поиска отвергнутых решений.

В Claude Code репозиторий служит маркетплейсом плагинов:

```text
/plugin marketplace add troyan-dy/nuke-di
/plugin install nuke-di@nuke-di
```

Codex, Cursor, GitHub Copilot и Gemini CLI читают скиллы в формате [Agent Skills](https://agentskills.io)
из каталога `.agents/skills/` проекта; Claude Code без плагина читает `.claude/skills/`:

```bash
mkdir -p .agents/skills/nuke-di
curl -fsSL https://raw.githubusercontent.com/troyan-dy/nuke-di/master/skills/nuke-di/SKILL.md \
  -o .agents/skills/nuke-di/SKILL.md
```

## <a id="a-block-for-agentsmd"></a>Блок для AGENTS.md

Агент без поддержки скиллов всё равно читает инструкции проекта. Вставьте этот блок в его
`AGENTS.md` (Codex, Cursor, Copilot) или `CLAUDE.md` (Claude Code):

```markdown
## Dependency injection: nuke-di

This project uses nuke-di. A dependency is a `nuke_di.Client` subclass whose `__init__` takes its own
dependencies as type-hinted arguments; I/O goes in `async def connect()` / `disconnect()`, never in `__init__`.
Functions, `@job` / `@worker` entrypoints and FastAPI / Litestar / FastStream handlers take clients by type hint.

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

Два файла в корне репозитория, сгенерированные из английских страниц:

| Файл | Содержимое |
|------|------------|
| [`llms.txt`](../../../llms.txt) | Оглавление в формате [llms.txt](https://llmstxt.org): что `nuke-di` делает и чего не делает, ссылка на каждую страницу |
| [`llms-full.txt`](../../../llms-full.txt) | README и всё руководство одним файлом, около 32 тысяч токенов |

Дайте агенту raw-ссылку `https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt`,
и он прочитает всё руководство за один запрос.

## <a id="context7"></a>Context7

[Context7](https://context7.com) отдаёт агенту документацию библиотеки через свой MCP-сервер.
`context7.json` в корне репозитория говорит ему, что индексировать: README, руководство и примеры,
без переводов. Когда сервер установлен, попросите его по имени: «use context7 for nuke-di».

## <a id="the-graph-as-json"></a>Граф в JSON

Агент проверяет написанную им проводку по [графу](clients.md#the-graph) контейнера, представленному
данными: каких клиентов строит entrypoint и от чего зависит каждый из них. Экспорта в JSON в библиотеке
нет, потому что `Graph.nodes` даёт его в несколько строк:

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

Узлы идут в порядке разрешения, поэтому клиент стоит после своих зависимостей и подключается тоже после
них. Replacement из `mock()` или `override()` сохраняет класс, который он подменяет, а тип объекта
лежит в `replacement`; Replacement никогда не подключается. Для приложения соберите контейнер так же,
как это делает entrypoint, — для джобы `deps.inject(sync)`, — и выведите JSON.

[Плагин для mypy](clients.md#checking-the-tree-with-mypy) и
[тест, который вызывает `inject()` для каждого entrypoint-а](testing.md), проверяют ту же проводку,
не читая её.
