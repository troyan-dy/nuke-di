import asyncio
import datetime
import sys
from pathlib import Path
from typing import Annotated

from nuke_di import Option, job

from cli_job.clients import Orders, log
from cli_job.report import Format, render


@job
async def export(  # noqa: WPS211 - every argument is a command-line Parameter, shown in --help
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
