# <a id="configuration"></a>Konfiguracja

[English](../../guide/configuration.md) · [Русский](../ru/configuration.md) · [简体中文](../zh-CN/configuration.md) · [Español](../es/configuration.md) · [Português (Brasil)](../pt-BR/configuration.md) · [日本語](../ja/configuration.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

| Zmienna środowiskowa         | Domyślnie | Opis                                             |
|------------------------------|-----------|--------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`      | Timeout `connect()` pojedynczego klienta, w sekundach |
| `CONNECT_CONCURRENCY`        | `0`       | Ilu klientów może jednocześnie łączyć się lub rozłączać w całym kontenerze; `0` oznacza brak limitu |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`      | Timeout `disconnect()` pojedynczego klienta, w sekundach |
| `SHUTDOWN_GRACE_SECONDS`     | `10`      | Jak długo worker lub job może jeszcze działać po SIGTERM / SIGINT, zanim zostanie anulowany, w sekundach; odczytywane przy starcie procesu |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

Ustawienia kontenera są odczytywane w chwili tworzenia instancji `Dependencies`. Można je też
przekazać jawnie:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```
