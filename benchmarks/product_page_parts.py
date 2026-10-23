"""
The parts of the picture of benchmarks/product_page.py: a box of the tree and a bar of a library.
"""

from html import escape
from typing import Final, TypeAlias

import product_page_layout as geometry
from compare_trees import PRODUCT_PAGE_TREE
from product_page_svg import Drawing
from suite.table import fmt

# The text of a box and of a bar sits this far below its top
_BOX_BASELINE: Final = 16
_BAR_BASELINE: Final = 13
# A library within this ratio of nuke-di takes the same time
_SAME_WITHIN: Final = 1.05

# Where the edges start and end
_LEAVES_RIGHT: Final = geometry.CONNECTIONS.right
_FEATURES_LEFT: Final = geometry.FEATURE_COLUMN.left
_FEATURES_RIGHT: Final = geometry.FEATURE_COLUMN.right
_API_LEFT: Final = geometry.API_COLUMN.left

# A bar: its label, its seconds and whether it is nuke-di's
BarRow: TypeAlias = tuple[str, float, bool]


def seconds(duration: float) -> str:
    return fmt(duration, "s")


class ProductPage(Drawing):
    """
    The picture: the boxes of the tree and the bars of the libraries, on the elements of a drawing.
    """

    def tree(self, tops: dict[str, float]) -> None:
        """
        The headings of the columns, the edges from every client to its consumers, then the boxes over them.
        """
        for column in geometry.COLUMNS:
            self.text(column.heading, class_="column", x=column.left, y=geometry.COLUMN_Y)
        self._edges(tops)
        for feature in geometry.FEATURES:
            for leaf in PRODUCT_PAGE_TREE[feature].dependencies:
                self.box(leaf, geometry.CONNECTIONS, tops[leaf])
            self.box(feature, geometry.FEATURE_COLUMN, tops[feature])
        self.box(geometry.ROOT_CLIENT, geometry.API_COLUMN, tops[geometry.ROOT_CLIENT])

    def box(self, name: str, column: geometry.Column, top: float) -> None:
        left, width, kind, _ = column
        self.rect(
            class_=kind,
            x=left,
            y=top,
            width=width,
            height=geometry.BOX_H,
            rx=5,
        )
        if name in geometry.SLOW_TO_STOP:
            self.rect(
                class_="slow-stop",
                x=left - 3,
                y=top - 3,
                width=width + 6,
                height=geometry.BOX_H + 6,
                rx=7,
            )
        if column is not geometry.API_COLUMN:
            # The connect() time as a strip inside the box
            strip = width * PRODUCT_PAGE_TREE[name].connect / geometry.FULL_BOX
            self.rect(
                class_="load",
                x=left,
                y=top,
                width=f"{strip:.1f}",
                height=geometry.BOX_H,
                rx=5,
            )
        self._box_text(name, column, top)

    def bars(self, rows: list[BarRow], top: float, scale: float) -> None:
        ours = next(duration for _, duration, highlight in rows if highlight)
        for number, row in enumerate(rows):
            self._row(row, top + number * geometry.BAR_STEP, scale, ours)

    def _edges(self, tops: dict[str, float]) -> None:
        # Every edge joins the middles of the sides of two boxes
        half = geometry.BOX_H / 2
        api = (_API_LEFT, tops[geometry.ROOT_CLIENT] + half)
        for feature in geometry.FEATURES:
            middle = tops[feature] + half
            for leaf in PRODUCT_PAGE_TREE[feature].dependencies:
                self.edge((_LEAVES_RIGHT, tops[leaf] + half), (_FEATURES_LEFT, middle))
            self.edge((_FEATURES_RIGHT, middle), api)

    def _box_text(self, name: str, column: geometry.Column, top: float) -> None:
        milliseconds = PRODUCT_PAGE_TREE[name].connect * 1000
        api = column is geometry.API_COLUMN
        # The API in its own colors, a little further from the edges
        padding = 10 if api else 9
        baseline = top + _BOX_BASELINE
        self.text(
            escape(name),
            class_="api-name" if api else "name",
            x=column.left + padding,
            y=baseline,
        )
        self.text(
            f"{milliseconds:.0f} ms",
            class_="api-time" if api else "time",
            x=column.left + column.width - padding,
            y=baseline,
            text_anchor="end",
        )

    def _row(self, row: BarRow, row_top: float, scale: float, ours: float) -> None:
        label, duration, highlight = row
        width = max(duration * scale, 2)
        baseline = row_top + _BAR_BASELINE
        self.text(escape(label), class_="label", x=geometry.MARGIN, y=baseline)
        self.rect(
            class_="ours" if highlight else "theirs",
            x=geometry.BAR_X,
            y=row_top,
            width=f"{width:.1f}",
            height=geometry.BAR_H,
            rx=3,
        )
        self.text(
            _figure(duration, duration / ours, highlight),
            class_="value",
            x=format(geometry.BAR_X + width + 8, ".1f"),
            y=baseline,
        )


def _figure(duration: float, times: float, highlight: bool) -> str:
    """
    The figure of a bar, then nothing on nuke-di's, the same or how much longer than nuke-di it takes.
    """
    if highlight:
        ratio = ""
    elif times < _SAME_WITHIN:
        ratio = "  the same"
    else:
        ratio = f"  {times:.1f}× longer"  # noqa: RUF001
    figure = seconds(duration)
    return f'{figure}<tspan class="ratio">{ratio}</tspan>'
