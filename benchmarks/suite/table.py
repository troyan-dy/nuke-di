"""
The Markdown table of the measurements: median and p95, and the figure per client where it makes sense.
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final, TypeAlias

from suite.measure import Measurement

# The units of a figure and what each one is worth, the largest first
_Scales: TypeAlias = tuple[tuple[str, float], ...]
_SCALES: Final[Mapping[str, _Scales]] = MappingProxyType(
    {
        "s": (("s", 1.0), ("ms", 1e-3), ("µs", 1e-6), ("ns", 1e-9)),
        "B": (("MB", 1e6), ("kB", 1e3), ("B", 1.0)),
    }
)


def significant(number: float) -> str:
    """
    Three significant digits of a number in [1, 1000).
    """
    if number >= 100:
        return f"{number:.0f}"
    if number >= 10:
        return f"{number:.1f}"
    return f"{number:.2f}"


def fmt(number: float, unit: str) -> str:
    scales = _SCALES[unit]
    reached = [each for each in scales if abs(number) >= each[1]]
    # The largest unit the number reaches, the smallest for a number below every one
    name, scale = reached[0] if reached else scales[-1]
    return f"{significant(number / scale)} {name}"


def table(measurements: Sequence[Measurement]) -> str:
    # The Library column only in a comparison
    libraries = any(measurement.library is not None for measurement in measurements)
    library_column = ["Library"] if libraries else []
    rows = [[*library_column, "Scenario", "Shape", "N", "Median", "p95", "Per client"]]
    rows.extend(_cells(measurement, libraries) for measurement in measurements)
    # Text columns left-aligned, numbers right-aligned
    numeric = [False for _ in library_column]
    numeric.extend((False, False, True, True, True, True))
    return markdown(rows, numeric)


def _cells(measurement: Measurement, libraries: bool) -> list[str]:
    """
    The row of one measurement: its library in a comparison, the figures or the error.
    """
    unit = measurement.unit
    if measurement.error is None:
        per_client = measurement.per_client_value
        figures = [fmt(measurement.median, unit), fmt(measurement.p95, unit)]
        figures.append("" if per_client is None else fmt(per_client, unit))
    else:
        figures = [measurement.error, "", ""]
    library = [measurement.library or ""] if libraries else []
    size = "" if measurement.size is None else str(measurement.size)
    return [*library, measurement.scenario, measurement.shape, size, *figures]


def markdown(rows: list[list[str]], numeric: list[bool], *, justified: bool = True) -> str:
    """
    `rows` as a Markdown table, the first one the header: the `numeric` columns right-aligned, in the cells unless
    not `justified`, and in the rule under the header.
    """
    columns = zip(*rows, strict=True)
    widths = [max(map(len, column)) for column in columns]
    alignment = numeric if justified else [False for _ in numeric]
    lines = [_line(row, widths, alignment) for row in rows]
    rule = "|".join(map(_rule, widths, numeric))
    lines.insert(1, f"|{rule}|")
    return "\n".join(lines)


def _line(row: list[str], widths: list[int], alignment: list[bool]) -> str:
    cells = [
        cell.rjust(width) if right else cell.ljust(width)
        for cell, width, right in zip(row, widths, alignment, strict=True)
    ]
    return "| {} |".format(" | ".join(cells))


def _rule(width: int, right: bool) -> str:
    if right:
        return ":".rjust(width + 2, "-")
    return "-" * (width + 2)
