# Errors

**English** · [Русский](../i18n/ru/errors.md) · [简体中文](../i18n/zh-CN/errors.md) · [Español](../i18n/es/errors.md) · [Português (Brasil)](../i18n/pt-BR/errors.md) · [日本語](../i18n/ja/errors.md) · [Polski](../i18n/pl/errors.md)

← [Documentation](../../README.md#documentation)

| Exception                   | Raised when                                               |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | A client's `__init__` raised                              |
| `ConnectError`              | A client's `connect()` raised, or the container state is wrong (e.g. resolving after connect, mocking a client that is already resolved, overriding a container that has resolved clients) |
| `ConnectTimeoutError`       | A client's `connect()` exceeded `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError`     | A client's `__init__` has a required argument that is not a client, `inject()` got a function with an argument without a type hint, `resolve()` got a class that is not a client, or an entrypoint parameter has an unsupported type or a clashing flag; see [When the tree cannot be built](clients.md#when-the-tree-cannot-be-built) |
| `CircularDependencyError`   | Clients depend on each other in a cycle; a subclass of `InvalidSignatureError` |
| `UsageError`                | The command line of a worker or a job does not match its parameters; recorded as `Run.error`, exit code `2` |

`InitializeDependencyError` and `ConnectError` derive from `SystemExit`: an application
whose dependencies cannot start is expected to stop. Catch them explicitly if you need
different behavior; the original exception is available as `__cause__`.

`nuke-di` logs through the standard `logging` module under the `nuke_di` logger, with
[structured fields](workers-and-jobs.md#startup-metrics-and-structured-logs) for log pipelines.
