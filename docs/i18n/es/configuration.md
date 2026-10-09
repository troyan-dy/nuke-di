# <a id="configuration"></a>Configuración

[English](../../guide/configuration.md) · [Русский](../ru/configuration.md) · [简体中文](../zh-CN/configuration.md) · **Español** · [Português (Brasil)](../pt-BR/configuration.md) · [日本語](../ja/configuration.md) · [Polski](../pl/configuration.md)

← [Documentación](../README.es.md#documentation)

| Variable de entorno          | Por defecto | Descripción                                        |
|------------------------------|-------------|----------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`        | Timeout del `connect()` de cada cliente, en segundos |
| `CONNECT_CONCURRENCY`        | `0`         | Cuántos clientes pueden conectarse o desconectarse a la vez en todo el contenedor; `0` significa sin límite |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`        | Timeout del `disconnect()` de cada cliente, en segundos |
| `SHUTDOWN_GRACE_SECONDS`     | `10`        | Cuánto tiempo puede seguir ejecutándose un worker o un job tras SIGTERM / SIGINT antes de cancelarse, en segundos; se lee al arrancar el proceso |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

La configuración del contenedor se lee al crear una instancia de `Dependencies`. También se puede
pasar de forma explícita:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```
