import csv
import dataclasses
import enum
import io
import json
from collections.abc import Sequence

from cli_job.clients import Order

# The columns of the CSV report, the fields of an order
FIELDS = tuple(field.name for field in dataclasses.fields(Order))


class Format(enum.Enum):
    CSV = "csv"
    JSON = "json"


def render(orders: Sequence[Order], fmt: Format) -> str:
    rows = [_row(order) for order in orders]
    if fmt is Format.JSON:
        text = json.dumps(rows, indent=2)
        return f"{text}\n"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _row(order: Order) -> dict[str, object]:
    # The day in ISO format, as the command line takes it
    return {**dataclasses.asdict(order), "created": order.created.isoformat()}
