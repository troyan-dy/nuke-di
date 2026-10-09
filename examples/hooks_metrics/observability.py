import json
import logging
import sys
from typing import Any

from nuke_di import Run

# The structured fields that nuke_di sets on its log records
FIELDS = ("run", "client", "duration")


class JsonFormatter(logging.Formatter):
    """One JSON object per record, with the structured fields of nuke_di as keys of their own."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        for key in FIELDS:
            if hasattr(record, key):
                value = getattr(record, key)
                entry[key] = round(value, 4) if isinstance(value, float) else value
        if record.exc_info and record.exc_info[1] is not None:
            entry["error"] = repr(record.exc_info[1])
        return json.dumps(entry)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])


class PrometheusMetrics:
    """
    A hook that turns a finished run into Prometheus text lines: the run's duration and exit code,
    and how long every client took to connect and disconnect. Printed here; push them to a
    Pushgateway or write them to a file for the node exporter in a real job.
    """

    async def on_start(self, run: Run) -> None:
        pass

    async def on_finish(self, run: Run) -> None:
        print("\n".join(render(run)))


def render(run: Run) -> list[str]:
    assert run.finished_at is not None  # set before on_finish
    job = f'job="{run.name}"'
    lines = [
        f"job_duration_seconds{{{job}}} {(run.finished_at - run.started_at).total_seconds():.3f}",
        f"job_exit_code{{{job}}} {run.exit_code}",
    ]
    for client in run.clients:
        labels = f'{job},client="{client.name}"'
        if client.connect is not None:
            outcome = f'outcome="{client.connect_outcome}"'
            lines.append(f"client_connect_seconds{{{labels},{outcome}}} {client.connect:.3f}")
        if client.disconnect is not None:
            outcome = f'outcome="{client.disconnect_outcome}"'
            lines.append(f"client_disconnect_seconds{{{labels},{outcome}}} {client.disconnect:.3f}")
    return lines
