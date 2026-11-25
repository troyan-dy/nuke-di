# Configuration

← [Documentation](../../README.md#documentation)

| Environment variable         | Default | Description                                        |
|------------------------------|---------|----------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`    | Timeout for a single client's `connect()`, seconds |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`    | Timeout for a single client's `disconnect()`, seconds |
| `DISCONNECT_DEADLINE_SECONDS` | `20`   | Budget of a whole `disconnect()`, and of the rollback of a failed `connect()`, seconds. Clients still disconnecting are timed out and clients that have not started are skipped. `inf` disables the deadline |

```bash
CONNECT_TIMEOUT_SECONDS=5 python -m app.report
```

The container settings are read when a `Dependencies` instance is created. You can also pass
them explicitly:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, disconnect_deadline=15))
```
