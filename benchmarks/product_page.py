"""
The picture of the product page in the README and docs/benchmarks.md, from the JSON of `compare.py --only connect`.

    uv run python benchmarks/product_page.py [JSON] [SVG]

The tree of benchmarks/compare_trees.py with the connect() time of every client, and the startup and the shutdown
of every library on one scale, nuke-di in color and the others in gray. Light and dark, as the other pictures of
docs/. Where everything goes is product_page_layout.py; the sections are product_page_sections.py.
"""

import json
import sys
from pathlib import Path
from typing import Any, Final

from compare_async import SHUTDOWN, STARTUP, STARTUP_GATHERED
from compare_trees import PRODUCT_PAGE
from product_page_layout import layout, tops
from product_page_parts import BarRow, ProductPage
from product_page_sections import DEPENDENCY_INJECTOR, DISHKA, NUKE_DI, WIREUP, Figures, bar_sections, header

ROOT: Final = Path(__file__).resolve().parent.parent
JSON: Final = ROOT / "docs" / "benchmarks" / "connect-py3.11.json"
SVG: Final = ROOT / "docs" / "product-page.svg"


def medians(report: dict[str, Any]) -> Figures:
    """
    The median of every library and scenario of the product page.
    """
    figures = {}
    for row in report["results"]:
        if row["shape"] == PRODUCT_PAGE and row["median"] is not None:
            figures[row["library"], row["scenario"]] = row["median"]
    return figures


def bar_rows(figures: Figures) -> tuple[list[BarRow], list[BarRow]]:
    """
    The bars of the startup and of the shutdown, nuke-di first.
    """
    startup = [
        (NUKE_DI, figures[NUKE_DI, STARTUP], True),
        ("dependency-injector, a Resource per client", figures[DEPENDENCY_INJECTOR, STARTUP], False),
        ("wireup, the features gathered by hand", figures[WIREUP, STARTUP_GATHERED], False),
        ("dishka, the same with its lock off", figures[DISHKA, STARTUP_GATHERED], False),
        (WIREUP, figures[WIREUP, STARTUP], False),
        (DISHKA, figures[DISHKA, STARTUP], False),
    ]
    shutdown = [
        (NUKE_DI, figures[NUKE_DI, SHUTDOWN], True),
        (DEPENDENCY_INJECTOR, figures[DEPENDENCY_INJECTOR, SHUTDOWN], False),
        (WIREUP, figures[WIREUP, SHUTDOWN], False),
        (DISHKA, figures[DISHKA, SHUTDOWN], False),
    ]
    return startup, shutdown


def draw(report: dict[str, Any]) -> str:
    figures = medians(report)
    startup, shutdown = bar_rows(figures)
    places = layout(tops(), len(startup), len(shutdown))
    page = ProductPage()
    header(page, figures, places.height)
    page.tree(places.tops)
    bar_sections(page, places, startup, shutdown)
    page.add("</svg>")
    return page.svg()


def main(argv: list[str]) -> int:
    source = Path(argv[0]) if argv else JSON
    target = Path(argv[1]) if len(argv) > 1 else SVG
    target.write_text(draw(json.loads(source.read_text())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
