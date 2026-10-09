# <a id="configuration"></a>Configuração

[English](../../guide/configuration.md) · [Русский](../ru/configuration.md) · [简体中文](../zh-CN/configuration.md) · [Español](../es/configuration.md) · **Português (Brasil)** · [日本語](../ja/configuration.md) · [Polski](../pl/configuration.md)

← [Documentação](../README.pt-BR.md#documentation)

| Variável de ambiente         | Padrão  | Descrição                                          |
|------------------------------|---------|----------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`    | Timeout do `connect()` de um único cliente, em segundos |
| `CONNECT_CONCURRENCY`        | `0`     | Quantos clientes podem se conectar ou desconectar ao mesmo tempo em todo o container; `0` significa sem limite |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`    | Timeout do `disconnect()` de um único cliente, em segundos |
| `SHUTDOWN_GRACE_SECONDS`     | `10`    | Por quanto tempo um worker ou job pode continuar rodando depois do SIGTERM / SIGINT antes de ser cancelado, em segundos; lido na inicialização do processo |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

As configurações do container são lidas quando uma instância de `Dependencies` é criada. Também é possível
passá-las explicitamente:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```
