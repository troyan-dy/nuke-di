from nuke_di import job

from hooks_metrics.clients import Kafka, Postgres
from hooks_metrics.observability import PrometheusMetrics, setup_logging

if __name__ == "__main__":  # a program configures logging; a test that imports the job does not
    setup_logging()


@job(hooks=[PrometheusMetrics()])
async def export(pg: Postgres, kafka: Kafka, limit: int = 3) -> None:
    """Publish the latest orders to Kafka."""
    for order in await pg.orders(limit):
        await kafka.send("orders", order)
