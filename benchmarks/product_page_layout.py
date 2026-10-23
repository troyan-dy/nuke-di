"""
Where everything of the picture of benchmarks/product_page.py goes: the columns of the tree, the rows of its
boxes and the bars below it.
"""

from dataclasses import dataclass
from typing import Final, NamedTuple

from compare_trees import PRODUCT_PAGE_TREE


class Column(NamedTuple):
    """
    A column of the tree: where its boxes start, how wide they are, their class and the heading over them.
    """

    left: int
    width: int
    kind: str
    heading: str

    @property
    def right(self) -> int:
        return self.left + self.width


ROOT_CLIENT: Final = "ProductPageApi"
# The features of the product page, the middle column
FEATURES: Final = PRODUCT_PAGE_TREE[ROOT_CLIENT].dependencies
WIDTH: Final = 880
# The margin of the text on the left, and of the rule over the bars on both sides
MARGIN: Final = 24
HEADING_Y: Final = 32
NOTE_Y: Final = 52
# The three columns of the tree, from the left: the connections, the features and the API
_COLUMN_ROWS: Final = (
    (24, 210, "leaf", "Connections"),
    (330, 170, "feature", "Features"),
    (600, 190, "api", "HTTP API"),
)
COLUMNS: Final = tuple(Column(*row) for row in _COLUMN_ROWS)
CONNECTIONS, FEATURE_COLUMN, API_COLUMN = COLUMNS
BOX_H: Final = 24
STEP: Final = 28
GAP: Final = 18
TREE_TOP: Final = 92
# The headings of the columns sit this far above the tree
_COLUMN_GAP: Final = 12
COLUMN_Y: Final = TREE_TOP - _COLUMN_GAP
# The bars, startup and shutdown on one scale; the longest leaves room on its right for its figure and ratio
BAR_X: Final = 300
_VALUE_ROOM: Final = 165
BAR_MAX: Final = WIDTH - BAR_X - _VALUE_ROOM
BAR_H: Final = 18
BAR_STEP: Final = 26
# The connect() time that fills a box
FULL_BOX: Final = 0.3
# A client that takes this long to stop is outlined: it is what the shutdowns differ by
SLOW_STOP: Final = 0.1
# The heading of a section of bars and the note under it, above its first bar
HEADING_GAP: Final = 22
# The rule over the bars sits this far above their heading
RULE_GAP: Final = 24
_BARS_GAP: Final = 56
_NOTE_HEIGHT: Final = 40
_BOTTOM: Final = 20


def _stops_slowly(name: str) -> bool:
    return PRODUCT_PAGE_TREE[name].disconnect >= SLOW_STOP and name != ROOT_CLIENT


# The clients outlined in the tree
SLOW_TO_STOP: Final = tuple(filter(_stops_slowly, PRODUCT_PAGE_TREE))


@dataclass(frozen=True)
class Layout:
    """
    The top of every box of the tree, then the tops of the sections of bars below it and the height of it all.
    """

    tops: dict[str, float]
    bars_top: float
    startup_top: float
    shutdown_heading: float
    shutdown_top: float
    height: float


def tops() -> dict[str, float]:
    """
    Every connection a row, grouped under its feature; a feature in the middle of its group, the API in the middle
    of the features.
    """
    found: dict[str, float] = {}
    row_top: float = TREE_TOP
    for feature in FEATURES:
        leaves = PRODUCT_PAGE_TREE[feature].dependencies
        for leaf in leaves:
            found[leaf] = row_top
            row_top += STEP
        found[feature] = _middle(found, leaves)
        row_top += GAP
    found[ROOT_CLIENT] = _middle(found, FEATURES)
    return found


def _middle(found: dict[str, float], names: tuple[str, ...]) -> float:
    """
    Halfway between the tops of the first and the last of `names`.
    """
    first = found[names[0]]
    last = found[names[-1]]
    return (first + last) / 2


def layout(boxes: dict[str, float], startup: int, shutdown: int) -> Layout:
    """
    The tree whose boxes start at `boxes`, then the bars of `startup` and of `shutdown` libraries below it.
    """
    # The last connection is the lowest box of the tree
    bars_top = max(boxes.values()) + BOX_H + _BARS_GAP
    startup_top = bars_top + HEADING_GAP
    shutdown_heading = startup_top + startup * BAR_STEP + HEADING_GAP
    shutdown_top = shutdown_heading + _NOTE_HEIGHT
    height = shutdown_top + shutdown * BAR_STEP + _BOTTOM
    return Layout(boxes, bars_top, startup_top, shutdown_heading, shutdown_top, height)


def chain(name: str, step: str) -> float:
    """
    The longest chain of `step` ("connect" or "disconnect") times from `name` down to a client with no dependencies.
    """
    node = PRODUCT_PAGE_TREE[name]
    below = max((chain(dependency, step) for dependency in node.dependencies), default=0)
    return float(getattr(node, step)) + below
