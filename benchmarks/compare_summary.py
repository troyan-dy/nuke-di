"""
The summary of benchmarks/compare.py: the figures that answer "which one is faster", one column per library.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from compare_async import REQUEST, SHUTDOWN, STARTUP
from compare_scenarios import COLD_START, WARM
from compare_trees import PRODUCT_PAGE
from libraries.compared import LIBRARIES
from suite.application import APPLICATION
from suite.measure import Measurement
from suite.table import fmt, markdown

# The size of a figure of the trees: the summary size, see summary_figures()
TREE_SIZE: Final = "N"
# Figures this close to the best are as good as the best
TIE: Final = 1.01


@dataclass(frozen=True)
class Figure:
    label: str
    scenario: str
    shape: str
    # None: not a tree figure; "N": the summary size
    size: int | str | None
    # Whether benchmarks/chart.py draws it: the chart keeps to the three figures that answer "which one is faster"
    chart: bool = True

    def sized_label(self, size: int) -> str:
        """
        The label with `size`, the summary size, for its N.
        """
        return self.label.replace(" N ", f" {size} ")

    def key(self, size: int) -> tuple[str, str, int | str | None]:
        """
        The scenario, the shape and the size of the measurements of the figure, `size` for the summary size.
        """
        if self.size == TREE_SIZE:
            return self.scenario, self.shape, size
        return self.scenario, self.shape, self.size


FIGURES: Final = (
    Figure("Cold start: a container and a tree of N clients", COLD_START, "mixed", TREE_SIZE),
    Figure(
        "Cold start: the same N clients with string annotations", COLD_START, "mixed, strings", TREE_SIZE, chart=False
    ),
    Figure("A cached root", WARM, "mixed", TREE_SIZE),
    Figure("A FastAPI request with a client", REQUEST, "FastAPI", None),
    Figure("Startup: 8 clients, connect() of 1–60 ms", STARTUP, APPLICATION, None, chart=False),  # noqa: RUF001
    Figure("Shutdown: the same 8 clients", SHUTDOWN, APPLICATION, None, chart=False),
    Figure("Startup: the product page, 21 clients", STARTUP, PRODUCT_PAGE, None, chart=False),
    Figure("Shutdown: the product page", SHUTDOWN, PRODUCT_PAGE, None, chart=False),
)

Medians = dict[str, float | None]


def summary_figures(
    measurements: Sequence[Measurement],
    sizes: list[int],
    wanted_figures: Sequence[Figure] = FIGURES,
) -> list[tuple[str, str, Medians]]:
    """
    For every figure: its label, its unit and the median per library, `None` where the library failed.
    """
    # The size of an ordinary application if measured, the largest otherwise
    size = 100 if 100 in sizes else max(sizes)
    figures = []
    for figure in wanted_figures:
        unit, medians = _medians(measurements, figure, size)
        if medians:
            figures.append((figure.sized_label(size), unit, medians))
    return figures


def _medians(measurements: Sequence[Measurement], figure: Figure, size: int) -> tuple[str, Medians]:
    """
    The unit of `figure` and the median of every library that has it, at `size` for a figure of the trees.
    """
    wanted = figure.key(size)
    medians: Medians = {}
    unit = "s"
    for measurement in measurements:
        if (measurement.scenario, measurement.shape, measurement.size) == wanted:
            assert measurement.library is not None
            medians[measurement.library] = measurement.median if measurement.error is None else None
            unit = measurement.unit
    return unit, medians


def pivot(measurements: Sequence[Measurement], sizes: list[int]) -> str:
    """
    One row per figure, one column per library: the best in bold, and every figure within 1% of it, a difference
    inside the noise of a run; the others with their ratio to the best.
    """
    libraries = [library.name for library in LIBRARIES]
    rows = [["Lower is better", *libraries]]
    for label, unit, medians in summary_figures(measurements, sizes):
        rows.append([label, *_cells(medians, libraries, unit)])
    return markdown(rows, [False, *(True for _ in libraries)], justified=False)


def _cells(medians: Medians, libraries: list[str], unit: str) -> list[str]:
    measured = [median for median in medians.values() if median is not None]
    best = min(measured) if measured else None
    return [_cell(medians.get(library), best, unit) for library in libraries]


def _cell(median: float | None, best: float | None, unit: str) -> str:
    if median is None:
        return "—"
    assert best is not None
    if median <= best * TIE:
        return f"**{fmt(median, unit)}**"
    ratio = median / best
    return f"{fmt(median, unit)} ({ratio:.1f}×)"  # noqa: RUF001
