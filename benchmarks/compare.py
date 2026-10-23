"""
nuke-di against other dependency injection libraries, on the trees of benchmarks/run.py.

    uv run --group compare python benchmarks/compare.py [--size N]... [--repeat K] [--only SCENARIO]... [--json PATH]

Every library gets the same classes, whose `__init__` takes the dependencies by type hint, and does the
same work: `cold` builds a container, registers the classes and gets the root of the tree, which
constructs every client, on classes made for every sample, as the cold row of benchmarks/run.py; `warm`
gets the root again from that container, the singleton; `request` is one FastAPI request to a handler
that takes a client through the library's integration; `connect` starts and stops clients whose
`connect()` and `disconnect()` take the time a real connection does, through the async lifecycle of each
library. The figures of the baseline are in docs/benchmarks.md.

The libraries are the `compare` dependency group: `uv sync --group compare`. Each one is a module of
benchmarks/libraries/; the scenarios are compare_scenarios.py and compare_async.py, the summary
compare_summary.py.
"""

import argparse
import sys
from types import MappingProxyType

from compare_async import connect_scenario, request_scenario
from compare_scenarios import cold_scenario, warm_scenario
from compare_summary import pivot
from compare_trees import allow_recursion
from libraries.compared import listed, versions
from suite.cli import options, read_options, run_scenarios
from suite.report import environment, summary, write_json
from suite.table import table

SCENARIOS = MappingProxyType(
    {
        "cold": cold_scenario,
        "warm": warm_scenario,
        "request": request_scenario,
        "connect": connect_scenario,
    }
)


def main(argv: list[str] | None = None) -> int:
    args, sizes = _arguments(argv)
    allow_recursion(max(sizes))
    env = environment(sizes, args.repeat)
    found = versions()
    env["libraries"] = found
    sys.stdout.write(f"{summary(env)}\n{listed(found)}\n\n")
    measurements = run_scenarios(SCENARIOS, args.only, sizes, args.repeat)
    if not args.summary:
        sys.stdout.write(f"{table(measurements)}\n\n")
    sys.stdout.write(f"{pivot(measurements, sizes)}\n")
    write_json(args.json, env, measurements)
    return 0


def _arguments(argv: list[str] | None) -> tuple[argparse.Namespace, list[int]]:
    parser = options("nuke-di against other DI libraries; see docs/benchmarks.md.", SCENARIOS)
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print only the summary: the best per figure and the ratios",
    )
    return read_options(parser, argv)


if __name__ == "__main__":
    sys.exit(main())
