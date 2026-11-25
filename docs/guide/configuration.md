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

When no settings are supplied, the container reads these environment variables at its first
`connect()`. This includes the global `DI`, so applications can load environment configuration
after importing it. The container keeps the first valid settings it reads, including across
a flush or a failed client connection. If the environment supplies an invalid setting, correct
it before retrying startup.

To supply settings explicitly, pass a `DependenciesSettings` object. Fields omitted when
constructing the object use their environment defaults at that point. Setting every field
explicitly makes the configuration independent of the environment.

`DependenciesSettings` rejects negative values and `NaN` with `ValueError`. Zero expires
immediately, and `inf` removes the corresponding limit.

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, disconnect_deadline=15))
```
