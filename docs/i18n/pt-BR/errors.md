# <a id="errors"></a>Erros

[English](../../guide/errors.md) · [Русский](../ru/errors.md) · [简体中文](../zh-CN/errors.md) · [Español](../es/errors.md) · **Português (Brasil)** · [日本語](../ja/errors.md) · [Polski](../pl/errors.md)

← [Documentação](../README.pt-BR.md#documentation)

| Exceção                     | Lançada quando                                            |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | O `__init__` de um cliente lançou uma exceção             |
| `ConnectError`              | O `connect()` de um cliente lançou uma exceção, ou o estado do container é inválido (por exemplo, resolver depois de conectar, mockar um cliente já resolvido, usar `override()` em um container com clientes resolvidos) |
| `ConnectTimeoutError`       | O `connect()` de um cliente excedeu `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError`     | O `__init__` de um cliente tem um argumento obrigatório que não é cliente, `inject()` recebeu uma função com um argumento sem type hint, `resolve()` recebeu uma classe que não é cliente, ou um parâmetro de ponto de entrada tem um tipo não suportado ou uma flag conflitante; veja [Quando a árvore não pode ser construída](clients.md#when-the-tree-cannot-be-built) |
| `CircularDependencyError`   | Clientes dependem uns dos outros em ciclo; subclasse de `InvalidSignatureError` |
| `UsageError`                | A linha de comando de um worker ou job não corresponde aos seus parâmetros; registrado em `Run.error`, código de saída `2` |

`InitializeDependencyError` e `ConnectError` herdam de `SystemExit`: espera-se que uma aplicação
cujas dependências não conseguem subir seja encerrada. Capture-as explicitamente se precisar de
outro comportamento; a exceção original fica disponível em `__cause__`.

O `nuke-di` faz log pelo módulo padrão `logging`, no logger `nuke_di`, com
[campos estruturados](workers-and-jobs.md#startup-metrics-and-structured-logs) para pipelines de logs.
