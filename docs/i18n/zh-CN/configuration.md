# <a id="configuration"></a>配置

[English](../../guide/configuration.md) · [Русский](../ru/configuration.md) · **简体中文** · [Español](../es/configuration.md) · [Português (Brasil)](../pt-BR/configuration.md) · [日本語](../ja/configuration.md) · [Polski](../pl/configuration.md)

← [文档](../README.zh-CN.md#documentation)

| 环境变量                     | 默认值  | 说明                                               |
|------------------------------|---------|----------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`    | 单个客户端 `connect()` 的超时时间，单位为秒        |
| `CONNECT_CONCURRENCY`        | `0`     | 整个容器内可同时连接或断开连接的客户端数量；`0` 表示不限制 |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`    | 单个客户端 `disconnect()` 的超时时间，单位为秒     |
| `SHUTDOWN_GRACE_SECONDS`     | `10`    | worker 或 job 在收到 SIGTERM / SIGINT 后、被取消之前还能继续运行的时间，单位为秒；在进程启动时读取 |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

容器设置在创建 `Dependencies` 实例时读取。也可以
显式传入：

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```
