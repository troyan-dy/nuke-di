"""
One panel of the comparison chart of benchmarks/chart.py: the libraries of one figure as bars, sorted from the
fastest, nuke-di in color and the others in gray.
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, Final

from libraries.compared import LIBRARIES
from matplotlib.axes import Axes
from suite.table import fmt

HIGHLIGHT: Final = "#2a78d6"
MUTED: Final = "#8f8e88"
TEXT: Final = "#0b0b0b"
SECONDARY: Final = "#52514e"
SURFACE: Final = "#fcfcfb"
LEFT: Final = "left"

_OURS: Final = "nuke-di"
_BAR_HEIGHT: Final = 0.62
# The label of a bar starts past its end by this share of the longest bar, which leaves room on its right for
# its own label
_LABEL_GAP: Final = 0.02
_HEADROOM: Final = 1.3
_TITLE: Final[Mapping[str, Any]] = MappingProxyType({"loc": LEFT, "color": TEXT, "fontsize": 10.5, "pad": 12})


def plot_panel(
    axis: Axes,
    label: str,
    unit: str,
    medians: Mapping[str, float | None],
) -> None:
    names, numbers = _ordered(medians)
    positions = list(reversed(range(len(names))))
    axis.set_facecolor(SURFACE)
    _bars(axis, positions, names, numbers, unit)
    _style(axis, label, positions, names, max(numbers))


def _ordered(
    medians: Mapping[str, float | None],
) -> tuple[list[str], list[float]]:
    """
    The libraries with a figure, the fastest first, and their figures.
    """
    measured = [library.name for library in LIBRARIES if medians.get(library.name) is not None]
    names = sorted(measured, key=lambda name: medians[name] or 0)
    return names, [medians[name] or 0 for name in names]


def _bars(
    axis: Axes,
    positions: list[int],
    names: list[str],
    numbers: list[float],
    unit: str,
) -> None:
    colors = [HIGHLIGHT if name == _OURS else MUTED for name in names]
    rectangles = axis.barh(positions, numbers, color=colors, height=_BAR_HEIGHT)
    for rectangle, name, number in zip(rectangles, names, numbers, strict=True):
        axis.text(
            rectangle.get_width() + max(numbers) * _LABEL_GAP,
            rectangle.get_y() + rectangle.get_height() / 2,
            fmt(number, unit),
            va="center",
            ha=LEFT,
            color=TEXT,
            fontweight=_weight(name),
        )


def _style(
    axis: Axes,
    label: str,
    positions: list[int],
    names: Sequence[str],
    longest: float,
) -> None:
    axis.set_yticks(positions)
    axis.set_yticklabels(names, color=TEXT)
    for tick, name in zip(axis.get_yticklabels(), names, strict=True):
        tick.set_fontweight(_weight(name))
    axis.set_xlim(0, longest * _HEADROOM)
    axis.set_xticks([])
    axis.set_title(label, **_TITLE)
    for side in ("top", "right", "bottom"):
        axis.spines[side].set_visible(False)
    axis.spines[LEFT].set_color(MUTED)
    axis.tick_params(axis="y", length=0)


def _weight(name: str) -> str:
    return "bold" if name == _OURS else "normal"
