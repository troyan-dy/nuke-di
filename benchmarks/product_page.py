"""
The picture of the product page in the README and docs/benchmarks.md, from the JSON of `compare.py --only connect`.

    uv run python benchmarks/product_page.py [JSON] [SVG]

The tree of benchmarks/compare.py with the connect() time of every client, and the startup and the shutdown
of every library on one scale, nuke-di in color and the others in gray. Light and dark, as the other pictures of
docs/.
"""

import json
import sys
from html import escape
from pathlib import Path
from typing import Any

from compare import PRODUCT_PAGE, PRODUCT_PAGE_TREE, SHUTDOWN, STARTUP, STARTUP_GATHERED
from run import fmt

ROOT = Path(__file__).resolve().parent.parent
JSON = ROOT / "docs" / "benchmarks" / "connect-py3.11.json"
SVG = ROOT / "docs" / "product-page.svg"

ROOT_CLIENT = "ProductPageApi"
WIDTH = 880
# The three columns of the tree: the connections, the features and the API
LEAF_X, LEAF_W = 24, 210
FEATURE_X, FEATURE_W = 330, 170
API_X, API_W = 600, 190
BOX_H, STEP, GAP = 24, 28, 18
TREE_TOP = 92
# The bars, startup and shutdown on one scale
BAR_X = 300
BAR_MAX = WIDTH - BAR_X - 165
BAR_H, BAR_STEP = 18, 26
# The connect() time that fills a box
FULL_BOX = 0.3
# A client that takes this long to stop is outlined: it is what the shutdowns differ by
SLOW_STOP = 0.1

STYLE = """<style>
svg {
  --surface: #fcfcfb; --border: #e4e3df; --text: #0b0b0b; --muted: #52514e; --grid: #ecebe7;
  --bar: #2a78d6; --other: #a3a29c; --edge: #c9c8c2; --node: #ffffff; --feature: #eef4fc; --api: #2a78d6;
  --stop: #eb6834;
}
@media (prefers-color-scheme: dark) {
  svg {
    --surface: #1a1a19; --border: #33332f; --text: #ffffff; --muted: #c3c2b7; --grid: #2a2a27;
    --bar: #3987e5; --other: #77766f; --edge: #4a4945; --node: #232321; --feature: #1d2a3b; --api: #3987e5;
    --stop: #d95926;
  }
}
text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: var(--text); }
.bg { fill: var(--surface); stroke: var(--border); }
.heading { font-size: 15px; font-weight: 600; }
.title { font-size: 13px; font-weight: 600; }
.note, .time, .column { fill: var(--muted); font-size: 11px; }
.name { font-size: 12px; }
.api-name { font-size: 12px; font-weight: 600; fill: #ffffff; }
.api-time { font-size: 11px; fill: #ffffff; }
.edge { fill: none; stroke: var(--edge); stroke-width: 1.2; }
.leaf { fill: var(--node); stroke: var(--border); }
.feature { fill: var(--feature); stroke: var(--border); }
.slow-stop { fill: none; stroke: var(--stop); stroke-width: 1.5; stroke-dasharray: 4 3; }
.api { fill: var(--api); }
.load { fill: var(--bar); opacity: 0.18; }
.ours { fill: var(--bar); }
.theirs { fill: var(--other); }
.label { font-size: 12px; }
.value { font-size: 12px; font-weight: 600; white-space: pre; }
.ratio { font-weight: 400; fill: var(--muted); }
.grid { stroke: var(--grid); stroke-width: 1; }
</style>"""

Bar = tuple[str, float, bool]


def seconds(value: float) -> str:
    return fmt(value, "s")


def chain(name: str, step: str) -> float:
    """
    The longest chain of `step` ("connect" or "disconnect") times from `name` down to a client with no dependencies.
    """
    node = PRODUCT_PAGE_TREE[name]
    below = max((chain(dependency, step) for dependency in node.dependencies), default=0.0)
    return float(getattr(node, step)) + below


def medians(data: dict[str, Any]) -> dict[tuple[str, str], float]:
    """
    The median of every library and scenario of the product page.
    """
    return {
        (row["library"], row["scenario"]): row["median"]
        for row in data["results"]
        if row["shape"] == PRODUCT_PAGE and row["median"] is not None
    }


class Drawing:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def add(self, part: str) -> None:
        self.parts.append(part)

    def edge(self, x1: float, y1: float, x2: float, y2: float) -> None:
        middle = (x1 + x2) / 2
        self.add(f'<path class="edge" d="M{x1},{y1:.1f} C{middle},{y1:.1f} {middle},{y2:.1f} {x2},{y2:.1f}"/>')

    def box(self, name: str, x: float, top: float, width: float, kind: str) -> None:
        node = PRODUCT_PAGE_TREE[name]
        connect = node.connect
        self.add(f'<rect class="{kind}" x="{x}" y="{top}" width="{width}" height="{BOX_H}" rx="5"/>')
        if node.disconnect >= SLOW_STOP and name != ROOT_CLIENT:
            self.add(
                f'<rect class="slow-stop" x="{x - 3}" y="{top - 3}" width="{width + 6}" height="{BOX_H + 6}" rx="7"/>'
            )
        time = f"{connect * 1000:.0f} ms"
        if kind == "api":
            self.add(f'<text class="api-name" x="{x + 10}" y="{top + 16}">{escape(name)}</text>')
            self.add(f'<text class="api-time" x="{x + width - 10}" y="{top + 16}" text-anchor="end">{time}</text>')
            return
        # The connect() time as a strip inside the box
        strip = width * connect / FULL_BOX
        self.add(f'<rect class="load" x="{x}" y="{top}" width="{strip:.1f}" height="{BOX_H}" rx="5"/>')
        self.add(f'<text class="name" x="{x + 9}" y="{top + 16}">{escape(name)}</text>')
        self.add(f'<text class="time" x="{x + width - 9}" y="{top + 16}" text-anchor="end">{time}</text>')

    def bars(self, rows: list[Bar], top: float, scale: float) -> None:
        ours = next(value for _, value, highlight in rows if highlight)
        for number, (label, value, highlight) in enumerate(rows):
            y = top + number * BAR_STEP
            self.add(f'<text class="label" x="24" y="{y + 13}">{escape(label)}</text>')
            width = max(value * scale, 2)
            kind = "ours" if highlight else "theirs"
            self.add(f'<rect class="{kind}" x="{BAR_X}" y="{y}" width="{width:.1f}" height="{BAR_H}" rx="3"/>')
            times = value / ours
            ratio = "" if highlight else ("  the same" if times < 1.05 else f"  {times:.1f}× longer")  # noqa: RUF001
            self.add(
                f'<text class="value" x="{BAR_X + width + 8:.1f}" y="{y + 13}">{seconds(value)}'
                f'<tspan class="ratio">{ratio}</tspan></text>'
            )


def draw(data: dict[str, Any]) -> str:
    figures = medians(data)
    features = list(PRODUCT_PAGE_TREE[ROOT_CLIENT].dependencies)
    slow_stops = [
        name for name, node in PRODUCT_PAGE_TREE.items() if node.disconnect >= SLOW_STOP and name != ROOT_CLIENT
    ]

    # Every connection a row, grouped under its feature; a feature in the middle of its group
    tops: dict[str, float] = {}
    y: float = TREE_TOP
    for feature in features:
        first = y
        for leaf in PRODUCT_PAGE_TREE[feature].dependencies:
            tops[leaf] = y
            y += STEP
        tops[feature] = (first + y - STEP) / 2
        y += GAP
    tree_bottom = y - GAP - STEP + BOX_H
    tops[ROOT_CLIENT] = (tops[features[0]] + tops[features[-1]]) / 2

    startup: list[Bar] = [
        ("nuke-di", figures["nuke-di", STARTUP], True),
        ("dependency-injector, a Resource per client", figures["dependency-injector", STARTUP], False),
        (f"wireup, the {len(features)} features gathered by hand", figures["wireup", STARTUP_GATHERED], False),
        ("dishka, the same with its lock off", figures["dishka", STARTUP_GATHERED], False),
        ("wireup", figures["wireup", STARTUP], False),
        ("dishka", figures["dishka", STARTUP], False),
    ]
    shutdown: list[Bar] = [
        ("nuke-di", figures["nuke-di", SHUTDOWN], True),
        ("dependency-injector", figures["dependency-injector", SHUTDOWN], False),
        ("wireup", figures["wireup", SHUTDOWN], False),
        ("dishka", figures["dishka", SHUTDOWN], False),
    ]
    scale = BAR_MAX / max(value for _, value, _ in startup + shutdown)
    bars_top = tree_bottom + 56
    startup_top = bars_top + 22
    shutdown_heading = startup_top + len(startup) * BAR_STEP + 22
    shutdown_top = shutdown_heading + 40
    height = shutdown_top + len(shutdown) * BAR_STEP + 20

    total = sum(node.connect for node in PRODUCT_PAGE_TREE.values())
    longest = chain(ROOT_CLIENT, "connect")

    svg = Drawing()
    svg.add(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height:.0f}" '
        f'viewBox="0 0 {WIDTH} {height:.0f}" role="img" aria-labelledby="t d">'
    )
    svg.add(f'<title id="t">A product page: {len(PRODUCT_PAGE_TREE)} clients, started by each library</title>')
    svg.add(
        f'<desc id="d">A product page API needs {len(features)} features, and every feature its own connections, '
        "which take 100 to 300 ms to connect. nuke-di connects every client as soon as its own dependencies have, "
        "so the startup "
        f"takes the longest chain, {seconds(figures['nuke-di', STARTUP])}; dishka and wireup connect one client "
        f"after another, {seconds(figures['dishka', STARTUP])}. dependency-injector starts as fast with a Resource per "
        f"client and stops layer by layer, {seconds(figures['dependency-injector', SHUTDOWN])} against "
        f"{seconds(figures['nuke-di', SHUTDOWN])}.</desc>"
    )
    svg.add(STYLE)
    svg.add(f'<rect class="bg" x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1:.0f}" rx="10"/>')
    svg.add(
        f'<text class="heading" x="24" y="32">A product page: {len(PRODUCT_PAGE_TREE)} clients, the time of each '
        "connect() on its box</text>"
    )
    svg.add(
        '<text class="note" x="24" y="52">Every client connects once the clients to its left have. '
        f"Along the longest chain that takes {longest:.2f} s; one client after another, {total:.2f} s.</text>"
    )
    for x, column in ((LEAF_X, "Connections"), (FEATURE_X, "Features"), (API_X, "HTTP API")):
        svg.add(f'<text class="column" x="{x}" y="{TREE_TOP - 12}">{column}</text>')

    def middle(name: str) -> float:
        return tops[name] + BOX_H / 2

    for feature in features:
        for leaf in PRODUCT_PAGE_TREE[feature].dependencies:
            svg.edge(LEAF_X + LEAF_W, middle(leaf), FEATURE_X, middle(feature))
        svg.edge(FEATURE_X + FEATURE_W, middle(feature), API_X, middle(ROOT_CLIENT))
    for feature in features:
        for leaf in PRODUCT_PAGE_TREE[feature].dependencies:
            svg.box(leaf, LEAF_X, tops[leaf], LEAF_W, "leaf")
        svg.box(feature, FEATURE_X, tops[feature], FEATURE_W, "feature")
    svg.box(ROOT_CLIENT, API_X, tops[ROOT_CLIENT], API_W, "api")

    svg.add(f'<line class="grid" x1="24" x2="{WIDTH - 24}" y1="{bars_top - 24}" y2="{bars_top - 24}"/>')
    svg.add(
        f'<text class="title" x="24" y="{bars_top + 4}">Startup: connect() of every client, median of 20 runs</text>'
    )
    svg.bars(startup, startup_top, scale)
    svg.add(f'<text class="title" x="24" y="{shutdown_heading + 4}">Shutdown: disconnect() of every client</text>')
    svg.add(
        f'<text class="note" x="24" y="{shutdown_heading + 22}">{" and ".join(slow_stops)}, outlined above, take '
        f"{seconds(max(PRODUCT_PAGE_TREE[name].disconnect for name in slow_stops))} each to stop, the others up to "
        f"{seconds(max(n.disconnect for k, n in PRODUCT_PAGE_TREE.items() if k not in slow_stops))}; "
        "every client disconnects after the clients that need it.</text>"
    )
    svg.bars(shutdown, shutdown_top, scale)
    svg.add("</svg>")
    return "\n".join(svg.parts) + "\n"


def main(argv: list[str]) -> int:
    source = Path(argv[0]) if argv else JSON
    target = Path(argv[1]) if len(argv) > 1 else SVG
    target.write_text(draw(json.loads(source.read_text())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
