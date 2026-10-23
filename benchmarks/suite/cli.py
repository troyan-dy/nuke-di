"""
The command line of benchmarks/run.py and benchmarks/compare.py: the options they share and the run of the
scenarios they pick.
"""

import argparse
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Final, TypeAlias

from suite.measure import Measurement

SIZES: Final = (10, 100, 1000)
REPEAT: Final = 20

# A scenario takes the sizes and the repeats and yields its figures
Scenario: TypeAlias = Callable[[list[int], int], Iterator[Measurement]]


def options(description: str, scenarios: Mapping[str, Scenario]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--size", type=int, action="append", metavar="N", help=f"tree size, repeatable; default {SIZES}"
    )
    parser.add_argument("--repeat", type=int, default=REPEAT, metavar="K", help=f"samples per figure, default {REPEAT}")
    names = ", ".join(scenarios)
    parser.add_argument(
        "--only",
        action="append",
        choices=scenarios,
        metavar="SCENARIO",
        help=f"run one scenario, repeatable: {names}",
    )
    parser.add_argument("--json", type=Path, metavar="PATH", help="also write the figures as JSON")
    return parser


def read_options(
    parser: argparse.ArgumentParser,
    argv: list[str] | None,
) -> tuple[argparse.Namespace, list[int]]:
    """
    The options of `argv` and the sizes, sorted and each once; an error exits as argparse does.
    """
    args = parser.parse_args(argv)
    sizes: list[int] = sorted(set(args.size or SIZES))
    if args.repeat < 1 or any(size < 2 for size in sizes):
        parser.error("--repeat must be at least 1 and every --size at least 2")
    return args, sizes


def run_scenarios(
    scenarios: Mapping[str, Scenario],
    only: Iterable[str] | None,
    sizes: list[int],
    repeat: int,
) -> list[Measurement]:
    """
    The figures of the scenarios named in `only`, every one without it, the name of each on stderr as it starts.
    """
    measurements: list[Measurement] = []
    for name in only or scenarios:
        sys.stderr.write(f"{name}...\n")
        measurements.extend(scenarios[name](sizes, repeat))
    return measurements
