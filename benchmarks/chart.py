"""
The comparison chart of docs/benchmarks.md from the JSON of benchmarks/compare.py.

    uv run python benchmarks/chart.py [JSON] [PNG]

One panel per figure of the summary (a cold start, a cached root, a FastAPI request), the libraries
sorted from the fastest, nuke-di in color and the others in gray; lower is better. A panel is drawn by
benchmarks/chart_panel.py.
"""

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from chart_panel import LEFT, SECONDARY, SURFACE, TEXT, plot_panel
from compare_summary import FIGURES, summary_figures
from libraries.compared import listed
from matplotlib import pyplot as plt
from matplotlib.figure import Figure
from suite.measure import Measurement

ROOT: Final = Path(__file__).resolve().parent.parent
JSON: Final = ROOT / "docs" / "benchmarks" / "compare-py3.11.json"
PNG: Final = ROOT / "docs" / "benchmarks" / "compare.png"

_CHARTED: Final = tuple(figure for figure in FIGURES if figure.chart)
# Inches, and the dots per inch of the PNG
_SIZE: Final = (14, 4.6)
_DPI: Final = 160
# The text of the figure starts flush with the panels
_MARGIN: Final = 0.135
_MARGINS: Final[Mapping[str, float]] = MappingProxyType(
    {
        "left": _MARGIN,
        "right": 0.975,
        "top": 0.7,
        "bottom": 0.14,
        "wspace": 0.95,
    }
)
_HEADING: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "x": _MARGIN,
        "ha": LEFT,
        "color": TEXT,
        "fontsize": 14,
        "fontweight": "bold",
        "y": 0.96,
    }
)
_VERSIONS: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "x": _MARGIN,
        "y": 0.865,
        "color": SECONDARY,
        "fontsize": 9,
        "va": "top",
    }
)
_ENVIRONMENT: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "x": _MARGIN,
        "y": 0.035,
        "color": SECONDARY,
        "fontsize": 8.5,
        "va": "bottom",
    }
)


def load(path: Path) -> tuple[dict[str, Any], list[Measurement]]:
    report = json.loads(path.read_text())
    measurements = [
        Measurement(
            row["scenario"],
            row["shape"],
            row["n"],
            row["samples"],
            row["unit"],
            per_client=row["per_client"] is not None,
            library=row["library"],
            error=row.get("error"),
        )
        for row in report["results"]
    ]
    return report, measurements


def draw(report: dict[str, Any], measurements: list[Measurement]) -> Figure:
    panels = summary_figures(measurements, report["sizes"], _CHARTED)
    figure, axes = _figure_layout(len(panels))
    for axis, panel in zip(axes, panels, strict=True):
        plot_panel(axis, *panel)
    _annotate(figure, report)
    return figure


def _figure_layout(panels: int) -> tuple[Figure, Any]:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    figure, axes = plt.subplots(1, panels, figsize=_SIZE, facecolor=SURFACE)
    figure.subplots_adjust(**_MARGINS)
    return figure, axes


def _annotate(figure: Figure, report: dict[str, Any]) -> None:
    figure.suptitle("nuke-di against other DI libraries: lower is better", **_HEADING)
    versions = listed(report["libraries"])
    environment = "{implementation} {python}, {platform}, median of {repeat} repeats".format_map(report)
    figure.text(s=versions, **_VERSIONS)
    figure.text(s=environment, **_ENVIRONMENT)


def main(argv: list[str]) -> int:
    source = Path(argv[0]) if argv else JSON
    target = Path(argv[1]) if len(argv) > 1 else PNG
    figure = draw(*load(source))
    figure.savefig(target, dpi=_DPI, facecolor=SURFACE)
    sys.stdout.write(f"{target}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
