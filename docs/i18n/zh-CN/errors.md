# <a id="errors"></a>错误

[English](../../guide/errors.md) · [Русский](../ru/errors.md) · **简体中文** · [Español](../es/errors.md) · [Português (Brasil)](../pt-BR/errors.md) · [日本語](../ja/errors.md) · [Polski](../pl/errors.md)

← [文档](../README.zh-CN.md#documentation)

| 异常                        | 抛出时机                                                  |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | 客户端的 `__init__` 抛出了异常                            |
| `ConnectError`              | 客户端的 `connect()` 抛出了异常，或容器状态不正确（例如在连接后解析、mock 一个已解析的客户端、对已有已解析客户端的容器调用 override） |
| `ConnectTimeoutError`       | 客户端的 `connect()` 超过了 `CONNECT_TIMEOUT_SECONDS`     |
| `InvalidSignatureError`     | 客户端的 `__init__` 有一个不是客户端的必需参数，`inject()` 收到的函数有参数缺少类型提示，`resolve()` 收到了不是客户端的类，或入口点参数的类型不受支持、选项名与已有选项冲突；参见[依赖树无法构建时](clients.md#when-the-tree-cannot-be-built) |
| `CircularDependencyError`   | 客户端之间存在循环依赖；是 `InvalidSignatureError` 的子类 |
| `UsageError`                | worker 或 job 的命令行与其参数不匹配；记录为 `Run.error`，退出码为 `2` |

`InitializeDependencyError` 和 `ConnectError` 继承自 `SystemExit`：依赖无法启动的应用
理应停止。如果需要不同的行为，请显式捕获它们；
原始异常可以通过 `__cause__` 获取。

`nuke-di` 通过标准的 `logging` 模块，以 `nuke_di` logger 输出日志，并带有供日志管道使用的
[结构化字段](workers-and-jobs.md#startup-metrics-and-structured-logs)。
