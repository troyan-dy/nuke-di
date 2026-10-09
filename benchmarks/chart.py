"""
The comparison chart of docs/benchmarks.md from the JSON of benchmarks/compare.py.

    uv run python benchmarks/chart.py [JSON] [PNG]

One panel per figure of the summary (a cold start, a cached root, a FastAPI request), the libraries
sorted from the fastest, nuke-di in color and the others in gray; lower is better.
"""

import json
import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from compare import FIGURES, LIBRARIES, summary_figures
from run import Result, fmt

ROOT = Path(__file__).resolve().parent.parent
JSON = ROOT / "docs" / "benchmarks" / "compare-py3.11.json"
PNG = ROOT / "docs" / "benchmarks" / "compare.png"

HIGHLIGHT = "#2a78d6"
MUTED = "#8f8e88"
TEXT = "#0b0b0b"
SECONDARY = "#52514e"
SURFACE = "#fcfcfb"


def load(path: Path) -> tuple[dict[str, object], list[Result]]:
    data = json.loads(path.read_text())
    results = [
        Result(
            row["scenario"],
            row["shape"],
            row["n"],
            row["samples"],
            row["unit"],
            per_client=row["per_client"] is not None,
            library=row["library"],
            error=row.get("error"),
        )
        for row in data["results"]
    ]
    return data, results


def draw(data: dict[str, object], results: list[Result]) -> matplotlib.figure.Figure:
    sizes: list[int] = data["sizes"]  # type: ignore[assignment]
    figures = summary_figures(results, sizes, [figure for figure in FIGURES if figure.chart])
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    figure, axes = plt.subplots(1, len(figures), figsize=(14, 4.6), facecolor=SURFACE)
    figure.subplots_adjust(left=0.135, right=0.975, top=0.70, bottom=0.14, wspace=0.95)

    for axis, (label, unit, medians) in zip(axes, figures, strict=True):
        ordered = sorted(
            ((library.name, medians[library.name]) for library in LIBRARIES if medians.get(library.name) is not None),
            key=lambda item: item[1] or 0,
        )
        names = [name for name, _ in ordered]
        values = [value or 0 for _, value in ordered]
        colors = [HIGHLIGHT if name == "nuke-di" else MUTED for name in names]
        positions = list(range(len(names)))[::-1]

        axis.set_facecolor(SURFACE)
        bars = axis.barh(positions, values, color=colors, height=0.62)
        for bar, name, value in zip(bars, names, values, strict=True):
            bold = "bold" if name == "nuke-di" else "normal"
            axis.text(
                bar.get_width() + max(values) * 0.02,
                bar.get_y() + bar.get_height() / 2,
                fmt(value, unit),
                va="center",
                ha="left",
                color=TEXT,
                fontweight=bold,
            )
        axis.set_yticks(positions)
        axis.set_yticklabels(names, color=TEXT)
        for tick, name in zip(axis.get_yticklabels(), names, strict=True):
            tick.set_fontweight("bold" if name == "nuke-di" else "normal")
        axis.set_xlim(0, max(values) * 1.3)
        axis.set_xticks([])
        axis.set_title(label, loc="left", color=TEXT, fontsize=10.5, pad=12)
        for side in ("top", "right", "bottom"):
            axis.spines[side].set_visible(False)
        axis.spines["left"].set_color(MUTED)
        axis.tick_params(axis="y", length=0)

    libraries: dict[str, str] = data["libraries"]  # type: ignore[assignment]
    figure.suptitle(
        "nuke-di against other DI libraries: lower is better",
        x=0.135,
        ha="left",
        color=TEXT,
        fontsize=14,
        fontweight="bold",
        y=0.96,
    )
    versions = " · ".join(f"{name} {version}" for name, version in libraries.items())
    environment = f"{data['implementation']} {data['python']}, {data['platform']}, median of {data['repeat']} repeats"
    figure.text(0.135, 0.865, versions, color=SECONDARY, fontsize=9, va="top")
    figure.text(0.135, 0.035, environment, color=SECONDARY, fontsize=8.5, va="bottom")
    return figure


def main(argv: list[str]) -> int:
    source = Path(argv[0]) if argv else JSON
    target = Path(argv[1]) if len(argv) > 1 else PNG
    data, results = load(source)
    figure = draw(data, results)
    figure.savefig(target, dpi=160, facecolor=SURFACE)
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
