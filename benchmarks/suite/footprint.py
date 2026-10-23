"""
The `import` and `memory` scenarios: the import time of the package and the memory of a resolved tree.
"""

import subprocess
import sys
import tracemalloc
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from importlib.util import find_spec
from pathlib import Path
from typing import Final

from nuke_di import Client, Dependencies
from suite.measure import Measurement, collect
from suite.shapes import mixed

ROOT: Final = Path(__file__).resolve().parent.parent.parent
# `-X importtime` reports microseconds
_MICROSECONDS: Final = 1_000_000


def import_seconds(module: str) -> float:
    """
    The cumulative import time of `module` in a fresh interpreter, from `-X importtime`.
    """
    # The interpreter of this run, with fixed arguments
    process = subprocess.run(  # noqa: S603
        [sys.executable, "-X", "importtime", "-c", f"import {module}"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    for line in process.stderr.splitlines():
        # import time:  self [us] | cumulative | imported package
        columns = [column.strip() for column in line.split("|")]
        if len(columns) == 3 and columns[2] == module:
            return int(columns[1]) / _MICROSECONDS
    raise RuntimeError(f"-X importtime did not report {module}:\n{process.stderr}")


def import_scenario(sizes: list[int], repeat: int) -> Iterator[Measurement]:
    modules = ["nuke_di"]
    if find_spec("fastapi") is not None:
        modules.extend(("nuke_di.fastapi", "fastapi"))
    for module in modules:
        yield Measurement("import, fresh interpreter", module, None, collect(partial(import_seconds, module), repeat))


@contextmanager
def traced() -> Iterator[None]:
    """
    tracemalloc on for the block.
    """
    tracemalloc.start()
    try:
        yield
    finally:
        tracemalloc.stop()


def peak_memory(root: type[Client]) -> float:
    with traced():
        Dependencies().resolve(root)
        _, peak = tracemalloc.get_traced_memory()
    return float(peak)


def memory_scenario(sizes: list[int], repeat: int) -> Iterator[Measurement]:
    size = max(sizes)
    tree = mixed(size)
    samples = collect(partial(peak_memory, tree.root), repeat)
    yield Measurement("resolve(), tracemalloc peak", "mixed", size, samples, unit="B")
