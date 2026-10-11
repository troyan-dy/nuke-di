# Configuration

← [Documentation](../../README.md#documentation)

| Environment variable         | Default | Description                                        |
|------------------------------|---------|----------------------------------------------------|
| `CONNECT_TIMEOUT_SECONDS`    | `30`    | Timeout for a single client's `connect()`, seconds |
| `CONNECT_CONCURRENCY`        | `0`     | How many clients may connect or disconnect at once across the container; `0` means no limit |
| `DISCONNECT_TIMEOUT_SECONDS` | `10`    | Timeout for a single client's `disconnect()`, seconds |
| `SHUTDOWN_GRACE_SECONDS`     | `10`    | How long a worker or a job may keep running after SIGTERM / SIGINT before it is cancelled, seconds; read when the process starts |

```bash
CONNECT_TIMEOUT_SECONDS=5 SHUTDOWN_GRACE_SECONDS=20 python -m app.workers.consumer
```

The container settings are read when a `Dependencies` instance is created. You can also pass
them explicitly:

```python
from nuke_di import Dependencies, DependenciesSettings

deps = Dependencies(settings=DependenciesSettings(connect_timeout=5, disconnect_timeout=5, connect_concurrency=4))
```
