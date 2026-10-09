import asyncio
import csv
import dataclasses
import datetime
import enum
import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from nuke_di import Option, job

from cli_job.clients import Order, Orders, log


class Format(enum.Enum):
    CSV = "csv"
    JSON = "json"


def render(orders: Sequence[Order], fmt: Format) -> str:
    rows = [dataclasses.asdict(order) | {"created": order.created.isoformat()} for order in orders]
    if fmt is Format.JSON:
        return json.dumps(rows, indent=2) + "\n"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=[field.name for field in dataclasses.fields(Order)], lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


@job
async def export(
    orders: Orders,
    since: Annotated[datetime.date, Option(help="First day of the report, YYYY-MM-DD", short="s")],
    limit: Annotated[int, Option(help="At most this many orders, newest first", short="n")] = 100,
    format: Annotated[Format, Option(help="Output format", short="f")] = Format.CSV,  # noqa: A002 - the name is the flag
    output: Annotated[Path | None, Option(help="File to write; stdout by default", short="o")] = None,
    dry_run: Annotated[bool, Option(help="Count the orders, write nothing")] = False,
) -> None:
    """Export the orders placed since a day as CSV or JSON."""
    await orders.ensure_demo_data()
    found = await orders.since(since, limit)
    destination = output or "stdout"
    if dry_run:
        log(f"export: would write {len(found)} orders to {destination}")
        return

    report = render(found, format)
    if output is None:
        sys.stdout.write(report)
        sys.stdout.flush()  # before the next line on stderr, so a terminal shows them in order
    else:
        await asyncio.to_thread(output.write_text, report)
    log(f"export: wrote {len(found)} orders to {destination}")
