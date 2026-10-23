"""
Benchmarks of nuke-di: what the library itself costs, measured on no-op clients.

    uv run python benchmarks/run.py [--size N]... [--repeat K] [--only SCENARIO]... [--json PATH]

Prints a Markdown table (median and p95 of K repeats, and the figure per client where it makes sense);
`--json` writes the same numbers with the Python version, platform and commit. The numbers of every
supported Python version are recorded in docs/benchmarks.md.

Only the standard library and nuke-di; the `fastapi` scenario needs fastapi and httpx and is skipped
without them. Garbage collection is paused while a sample runs, as `timeit` does. The scenarios are in
benchmarks/suite/; this module holds what awaits one coroutine after another, which is what it times.
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable, Iterable
from functools import partial

from suite.cli import options, read_options, run_scenarios
from suite.infrastructure import Sleeper
from suite.measure import timed
from suite.report import environment, summary, write_json
from suite.scenarios import scenarios
from suite.table import table
from suite.web import Get, serving

from nuke_di import NotSingletonClient


async def _in_order(clients: list[NotSingletonClient]) -> None:
    for instance in clients:
        await instance.connect()
    for instance in reversed(clients):
        await instance.disconnect()


# What the same clients cost without the container: every coroutine awaited directly, in order
ideal = partial(timed, _in_order)


async def after(
    tasks: dict[int, asyncio.Task[None]],
    others: Iterable[Sleeper],
    step: Callable[[], Awaitable[None]],
) -> None:
    """
    `step()` once the tasks of `others` are done.
    """
    for other in others:
        await tasks[id(other)]
    await step()


async def critical_path(clients: list[Sleeper]) -> float:
    """
    The same coroutines scheduled by hand: every connect() starts as soon as the client's dependencies are
    connected and every disconnect() as soon as its consumers are disconnected, so the wall time is the
    longest chain of the tree, the least any schedule can take.
    """
    consumers: dict[int, list[Sleeper]] = {id(client): [] for client in clients}
    for client in clients:
        for dependency in client.dependencies:
            consumers[id(dependency)].append(client)
    return await timed(_schedule, clients, consumers)


async def _schedule(clients: list[Sleeper], consumers: dict[int, list[Sleeper]]) -> None:
    # A task body runs only when the loop gets control back, after every task is created, so each one
    # finds the tasks it waits for whatever the order
    connects: dict[int, asyncio.Task[None]] = {}
    for client in clients:
        step = after(connects, client.dependencies, client.connect)
        connects[id(client)] = asyncio.create_task(step)
    await asyncio.gather(*connects.values())
    disconnects: dict[int, asyncio.Task[None]] = {}
    for client in clients:
        step = after(disconnects, consumers[id(client)], client.disconnect)
        disconnects[id(client)] = asyncio.create_task(step)
    await asyncio.gather(*disconnects.values())


async def request_samples(app: object, path: str, repeat: int, loops: int = 200) -> list[float]:
    """
    Seconds per `GET path` on the FastAPI `app` through the ASGI transport of httpx, `repeat` samples of
    `loops` requests each after a warm-up, with the lifespan of the app running as on a server.
    """
    async with serving(app, path) as get:
        samples = []
        for _ in range(repeat):
            seconds = await timed(_requests, get, path, loops)
            samples.append(seconds / loops)
        return samples


async def _requests(get: Get, path: str, loops: int) -> None:
    for _ in range(loops):
        await get(path)


SCENARIOS = scenarios(ideal, critical_path, request_samples)


def main(argv: list[str] | None = None) -> int:
    parser = options("Measure what nuke-di costs; see docs/benchmarks.md.", SCENARIOS)
    args, sizes = read_options(parser, argv)
    env = environment(sizes, args.repeat)
    sys.stdout.write(f"{summary(env)}\n\n")
    measurements = run_scenarios(SCENARIOS, args.only, sizes, args.repeat)
    sys.stdout.write(f"{table(measurements)}\n")
    write_json(args.json, env, measurements)
    return 0


if __name__ == "__main__":
    sys.exit(main())
