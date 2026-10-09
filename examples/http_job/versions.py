import asyncio
from typing import Annotated

from nuke_di import Option, job

from http_job.clients import PyPI


@job
async def versions(
    pypi: PyPI,
    package: Annotated[list[str], Option(help="A package name, repeat for several", short="p")],
) -> None:
    """Print the latest version of every package from the PyPI JSON API."""
    async with asyncio.TaskGroup() as group:  # the requests run concurrently; the first failure cancels the rest
        tasks = [group.create_task(pypi.latest_version(name)) for name in package]
    for name, task in zip(package, tasks, strict=True):
        print(f"{name}: {task.result()}")
