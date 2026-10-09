import datetime
import json
import logging
from unittest.mock import call

import pytest
from nuke_di import ClientTiming, Dependencies, Run

from hooks_metrics.clients import Kafka, Postgres
from hooks_metrics.export import export
from hooks_metrics.observability import JsonFormatter, PrometheusMetrics


async def test_metrics_of_a_failed_run(capsys: pytest.CaptureFixture[str]) -> None:
    started = datetime.datetime(2026, 10, 9, 6, 0, tzinfo=datetime.UTC)
    run = Run(
        name="app.jobs.export",
        kind="job",
        started_at=started,
        finished_at=started + datetime.timedelta(seconds=1.5),
        exit_code=1,
        clients=[
            ClientTiming("Postgres", connect=0.1, connect_outcome="ok", disconnect=0.0, disconnect_outcome="ok"),
            ClientTiming("Kafka", connect=1.0, connect_outcome="timed_out"),
            ClientTiming("Orders"),  # never started: no line
        ],
    )

    await PrometheusMetrics().on_finish(run)

    assert capsys.readouterr().out.splitlines() == [
        'job_duration_seconds{job="app.jobs.export"} 1.500',
        'job_exit_code{job="app.jobs.export"} 1',
        'client_connect_seconds{job="app.jobs.export",client="Postgres",outcome="ok"} 0.100',
        'client_disconnect_seconds{job="app.jobs.export",client="Postgres",outcome="ok"} 0.000',
        'client_connect_seconds{job="app.jobs.export",client="Kafka",outcome="timed_out"} 1.000',
    ]


def test_json_formatter_keeps_the_structured_fields() -> None:
    record = logging.makeLogRecord(
        {
            "name": "nuke_di.core",
            "levelname": "WARNING",
            "msg": "slow",
            "client": "Kafka",
            "duration": 0.61234,
        }
    )

    assert json.loads(JsonFormatter().format(record)) == {
        "level": "WARNING",
        "logger": "nuke_di.core",
        "message": "slow",
        "client": "Kafka",
        "duration": 0.6123,
    }


async def test_export_through_a_container(di: Dependencies) -> None:
    di.mock(Postgres).orders.return_value = ["order-1", "order-2"]
    kafka = di.mock(Kafka)
    injected = di.inject(export)

    async with di:
        await injected(limit=2)

    assert kafka.send.await_args_list == [call("orders", "order-1"), call("orders", "order-2")]
