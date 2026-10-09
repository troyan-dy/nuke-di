# <a id="configuration"></a>Настройка

[English](../../guide/configuration.md) · **Русский** · [简体中文](../zh-CN/configuration.md) · [Español](../es/configuration.md) · [Português (Brasil)](../pt-BR/configuration.md) · [日本語](../ja/configuration.md) · [Polski](../pl/configuration.md)

← [Документация](../README.ru.md#documentation)

| Переменная окружения         | По умолчанию | Описание                                      |
|------------------------------|--------------|-----------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`         | Таймаут `connect()` одного клиента, в секундах |
| `CONNECT_CONCURRENCY`        | `0`          | Сколько клиентов могут одновременно подключаться или отключаться в пределах контейнера; `0` — без ограничений |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`         | Таймаут `disconnect()` одного клиента, в секундах |
| `SHUTDOWN_GRACE_SECONDS`     | `10`         | Сколько воркер или джоба могут работать после SIGTERM / SIGINT, прежде чем их отменят, в секундах; читается при старте процесса |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

Настройки контейнера читаются при создании экземпляра `Dependencies`. Их можно передать
и явно:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```
