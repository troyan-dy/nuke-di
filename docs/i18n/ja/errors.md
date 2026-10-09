# <a id="errors"></a>エラー

[English](../../guide/errors.md) · [Русский](../ru/errors.md) · [简体中文](../zh-CN/errors.md) · [Español](../es/errors.md) · [Português (Brasil)](../pt-BR/errors.md) · **日本語** · [Polski](../pl/errors.md)

← [ドキュメント](../README.ja.md#documentation)

| 例外                        | 送出される条件                                            |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | クライアントの `__init__` が例外を送出した                |
| `ConnectError`              | クライアントの `connect()` が例外を送出した、またはコンテナの状態が不正（接続後の解決、解決済みクライアントのモック、解決済みクライアントを持つコンテナでの override など） |
| `ConnectTimeoutError`       | クライアントの `connect()` が `CONNECT_TIMEOUT_SECONDS` を超えた |
| `InvalidSignatureError`     | クライアントの `__init__` にクライアントではない必須引数がある、`inject()` に型ヒントのない引数を持つ関数が渡された、`resolve()` にクライアントではないクラスが渡された、またはエントリーポイントのパラメータがサポートされない型であるかフラグが衝突している。[ツリーを構築できない場合](clients.md#when-the-tree-cannot-be-built)を参照 |
| `CircularDependencyError`   | クライアント同士が循環して依存している。`InvalidSignatureError` のサブクラス |
| `UsageError`                | ワーカーやジョブのコマンドラインがパラメータと一致しない。`Run.error` に記録され、終了コードは `2` |

`InitializeDependencyError` と `ConnectError` は `SystemExit` を継承しています。依存関係を起動できないアプリケーションは停止すべきだ、という前提によるものです。別の動作が必要な場合は明示的に捕捉してください。元の例外は `__cause__` で参照できます。

`nuke-di` は標準の `logging` モジュールを使い、`nuke_di` ロガーにログを出力します。ログパイプライン向けの
[構造化フィールド](workers-and-jobs.md#startup-metrics-and-structured-logs)付きです。
