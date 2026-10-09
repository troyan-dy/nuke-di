# <a id="configuration"></a>設定

[English](../../guide/configuration.md) · [Русский](../ru/configuration.md) · [简体中文](../zh-CN/configuration.md) · [Español](../es/configuration.md) · [Português (Brasil)](../pt-BR/configuration.md) · **日本語** · [Polski](../pl/configuration.md)

← [ドキュメント](../README.ja.md#documentation)

| 環境変数                     | デフォルト | 説明                                               |
|------------------------------|------------|----------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`       | クライアント 1 つあたりの `connect()` のタイムアウト（秒） |
| `CONNECT_CONCURRENCY`        | `0`        | コンテナ全体で同時に接続・切断できるクライアントの数。`0` は無制限 |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`       | クライアント 1 つあたりの `disconnect()` のタイムアウト（秒） |
| `SHUTDOWN_GRACE_SECONDS`     | `10`       | SIGTERM / SIGINT を受け取ってから、ワーカーやジョブがキャンセルされるまで実行を続けられる時間（秒）。プロセスの起動時に読み込まれます |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

コンテナの設定は、`Dependencies` インスタンスの作成時に読み込まれます。明示的に渡すこともできます。

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```
