"""
The sections of the picture of benchmarks/product_page.py, from the top: the heading, the tree, and the bars of
the startup and of the shutdown.
"""

from collections.abc import Mapping
from typing import Final

import product_page_layout as geometry
from compare_async import SHUTDOWN, STARTUP
from compare_trees import PRODUCT_PAGE_TREE
from product_page_parts import BarRow, ProductPage, seconds
from product_page_svg import STYLE, element

NUKE_DI: Final = "nuke-di"
DISHKA: Final = "dishka"
WIREUP: Final = "wireup"
DEPENDENCY_INJECTOR: Final = "dependency-injector"

# The median of a library and a scenario
Figures = Mapping[tuple[str, str], float]

_OPENING: Final = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
    'viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d">'
)
_TREE_NOTE: Final = (
    "Every client connects once the clients to its left have. "
    "Along the longest chain that takes {longest:.2f} s; one client after another, {total:.2f} s."
)
_SHUTDOWN_NOTE: Final = (
    "{names}, outlined above, take {slowest} each to stop, the others up to {others:.0f} ms; "
    "every client disconnects after the clients that need it."
)


def header(page: ProductPage, figures: Figures, height: float) -> None:
    size = format(height, ".0f")
    page.add(_OPENING.format(width=geometry.WIDTH, height=size))
    clients = len(PRODUCT_PAGE_TREE)
    page.add(element("title", f"A product page: {clients} clients, started by each library", id="t"))
    page.add(_description(figures))
    page.add(STYLE)
    page.rect(
        class_="bg",
        x=0.5,
        y=0.5,
        width=geometry.WIDTH - 1,
        height=format(height - 1, ".0f"),
        rx=10,
    )
    heading = f"A product page: {clients} clients, the time of each connect() on its box"
    page.text(heading, class_="heading", x=geometry.MARGIN, y=geometry.HEADING_Y)
    total = sum(node.connect for node in PRODUCT_PAGE_TREE.values())
    note = _TREE_NOTE.format(longest=geometry.chain(geometry.ROOT_CLIENT, "connect"), total=total)
    page.text(note, class_="note", x=geometry.MARGIN, y=geometry.NOTE_Y)


def _description(figures: Figures) -> str:
    """
    The `<desc>` of the picture: what it shows, in words.
    """
    ours = seconds(figures[NUKE_DI, STARTUP])
    one_by_one = seconds(figures[DISHKA, STARTUP])
    layered = seconds(figures[DEPENDENCY_INJECTOR, SHUTDOWN])
    stop = seconds(figures[NUKE_DI, SHUTDOWN])
    return (
        f'<desc id="d">A product page API needs {len(geometry.FEATURES)} features, and every feature its own '
        "connections, which take 100 to 300 ms to connect. nuke-di connects every client as soon as its own "
        "dependencies have, so the startup "
        f"takes the longest chain, {ours}; dishka and wireup connect one client "
        f"after another, {one_by_one}. dependency-injector starts as fast with a Resource per "
        f"client and stops layer by layer, {layered} against "
        f"{stop}.</desc>"
    )


def bar_sections(page: ProductPage, places: geometry.Layout, startup: list[BarRow], shutdown: list[BarRow]) -> None:
    """
    The startup and the shutdown of every library, on one scale.
    """
    rows = (*startup, *shutdown)
    scale = geometry.BAR_MAX / max(duration for _, duration, _ in rows)
    rule = places.bars_top - geometry.RULE_GAP
    page.add(
        element(
            "line",
            class_="grid",
            x1=geometry.MARGIN,
            x2=geometry.WIDTH - geometry.MARGIN,
            y1=rule,
            y2=rule,
        ),
    )
    _title(page, "Startup: connect() of every client, median of 20 runs", places.bars_top)
    page.bars(startup, places.startup_top, scale)
    _title(page, "Shutdown: disconnect() of every client", places.shutdown_heading)
    note_top = places.shutdown_heading + geometry.HEADING_GAP
    page.text(_shutdown_note(), class_="note", x=geometry.MARGIN, y=note_top)
    page.bars(shutdown, places.shutdown_top, scale)


def _title(page: ProductPage, title: str, top: float) -> None:
    page.text(title, class_="title", x=geometry.MARGIN, y=top + 4)


def _shutdown_note() -> str:
    """
    The note over the shutdown bars: the clients that are slow to stop, outlined in the tree, against the others.
    """
    others = dict(PRODUCT_PAGE_TREE)
    slow = [others.pop(name).disconnect for name in geometry.SLOW_TO_STOP]
    rest = [node.disconnect for node in others.values()]
    return _SHUTDOWN_NOTE.format(
        names=" and ".join(geometry.SLOW_TO_STOP),
        slowest=seconds(max(slow)),
        others=max(rest) * 1000,
    )
