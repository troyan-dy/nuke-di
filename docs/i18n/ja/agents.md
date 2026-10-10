# <a id="coding-agents"></a>コーディングエージェント

[English](../../guide/agents.md) · [Русский](../ru/agents.md) · [简体中文](../zh-CN/agents.md) · [Español](../es/agents.md) · [Português (Brasil)](../pt-BR/agents.md) · **日本語** · [Polski](../pl/agents.md)

← [ドキュメント](../README.ja.md#documentation)

`nuke-di` について何も読んでいないコーディングエージェント（Claude Code、Codex、Cursor、Copilot、Gemini CLI）は、
他の DI ライブラリの流儀でコードを書きます。プロバイダー関数、`bind(Protocol, Impl)`、リクエストスコープ、
`connect()` の中のリトライなどです。これらはどれも、意図的にここには存在しません。以下のいずれかをエージェントに
渡せば、代わりにクライアントを書くようになります。

| 何を                                  | 対象                                                        |
|---------------------------------------|-------------------------------------------------------------|
| [Agent Skill](#the-agent-skill)       | Claude Code ではプラグインとして、Codex、Cursor、Copilot、Gemini CLI ではファイルとして |
| [AGENTS.md 用のブロック](#a-block-for-agentsmd) | `AGENTS.md` または `CLAUDE.md` を読むすべてのエージェント |
| [llms.txt](#llmstxt)                  | URL でドキュメントを取得するエージェント                    |
| [Context7](#context7)                 | Context7 の MCP サーバーを使うエージェント                  |
| [JSON としての依存グラフ](#the-graph-as-json) | 自分が書いた配線を確認するエージェント              |

## <a id="the-agent-skill"></a>Agent Skill

[`skills/nuke-di/SKILL.md`](../../../skills/nuke-di/SKILL.md) は、このモデルを 1 ページにまとめたものです。クライアントとは何か、
よくあるタスクのレシピ、書いてはいけないものとその理由の表、そして結果の確認方法が載っています。エージェントは、
タスクが `nuke_di` に関わるときにこれを読み込みます。

効果は仮定ではなく計測されています。[benchmarks/agents](../../../benchmarks/agents/README.md) は、サンプルを題材にした
コーディングタスクをスキルありとスキルなしでエージェントに与え、それぞれの結果を mypy、pytest、そして採用されなかった設計の
検索によって評価します。

Claude Code では、このリポジトリがプラグインマーケットプレイスになっています：

```text
/plugin marketplace add troyan-dy/nuke-di
/plugin install nuke-di@nuke-di
```

Codex、Cursor、GitHub Copilot、Gemini CLI は、プロジェクトの `.agents/skills/` から [Agent Skills](https://agentskills.io)
形式のスキルを読み込みます。プラグインを使わない Claude Code は `.claude/skills/` を読み込みます：

```bash
mkdir -p .agents/skills/nuke-di
curl -fsSL https://raw.githubusercontent.com/troyan-dy/nuke-di/master/skills/nuke-di/SKILL.md \
  -o .agents/skills/nuke-di/SKILL.md
```

## <a id="a-block-for-agentsmd"></a>AGENTS.md 用のブロック

スキルに対応していないエージェントでも、プロジェクトの指示は読みます。次の内容を `AGENTS.md`（Codex、Cursor、Copilot）
または `CLAUDE.md`（Claude Code）に貼り付けてください：

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

リポジトリのルートにある 2 つのファイルで、英語のページから生成されています：

| ファイル | 内容 |
|----------|------|
| [`llms.txt`](../../../llms.txt) | [llms.txt](https://llmstxt.org) 形式の索引。`nuke-di` が何をして何をしないか、そしてすべてのページへのリンク |
| [`llms-full.txt`](../../../llms-full.txt) | README とガイド全体を 1 つにまとめたファイル。約 35k トークン |

エージェントに raw URL `https://raw.githubusercontent.com/troyan-dy/nuke-di/master/llms-full.txt` を渡せば、
1 回の取得でマニュアル全体を読み込めます。

## <a id="context7"></a>Context7

[Context7](https://context7.com) は、ライブラリのドキュメントを MCP サーバー経由でエージェントに提供します。
リポジトリのルートにある `context7.json` が、インデックスの対象を指定します。README、ガイド、サンプルで、翻訳は含みません。
サーバーをインストールしたら、名前を挙げて依頼してください："use context7 for nuke-di"。

## <a id="the-graph-as-json"></a>JSON としての依存グラフ

エージェントは自分が書いた配線を、コンテナの[依存グラフ](clients.md#the-graph)からデータとして確認できます。
エントリーポイントがどのクライアントを構築し、それぞれが何に依存しているかです。ライブラリに JSON エクスポートはありません。
`Graph.nodes` を使えば数行で書けるからです：

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

ノードは解決順に並ぶので、クライアントは自分の依存の後に来ます。接続もその後に行われます。`mock()` や `override()` による
Replacement は、代わりを務めるクラスをそのまま持ち、`replacement` にはそのオブジェクトの型が入ります。Replacement が
接続されることはありません。アプリケーションでは、エントリーポイントと同じようにコンテナを構築してから（ジョブなら
`deps.inject(sync)`）、JSON を出力してください。

[mypy プラグイン](clients.md#checking-the-tree-with-mypy)と
[すべてのエントリーポイントを注入するテスト](testing.md)は、同じ配線をそれを読むことなく確認します。
