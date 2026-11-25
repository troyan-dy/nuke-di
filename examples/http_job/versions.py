import argparse
import asyncio

from nuke_di import Dependencies

from http_job.clients import PyPI


async def versions(pypi: PyPI, package: list[str]) -> None:
    """Print the latest version of every package from the PyPI JSON API."""
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(pypi.latest_version(name)) for name in package]
    for name, task in zip(package, tasks, strict=True):
        print(f"{name}: {task.result()}")


async def main(package: list[str]) -> None:
    deps = Dependencies()
    show_versions = deps.inject(versions)
    async with deps:
        await show_versions(package=package)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Print the latest versions from PyPI.")
    parser.add_argument("-p", "--package", action="append", required=True, help="Package name; repeat for several")
    args = parser.parse_args()
    asyncio.run(main(args.package))
