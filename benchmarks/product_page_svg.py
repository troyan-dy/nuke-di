"""
How the picture of benchmarks/product_page.py is written: its style and its elements, light and dark, as the other
pictures of docs/.
"""

from typing import Final

STYLE: Final = """<style>
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
# A cubic curve, level at both ends: its control points halfway across
_CURVE: Final = "M{start[0]},{start[1]:.1f} C{middle},{start[1]:.1f} {middle},{end[1]:.1f} {end[0]},{end[1]:.1f}"


def element(tag: str, text: str | None = None, /, **attributes: object) -> str:
    """
    An SVG element with `attributes` in their order, empty without `text`. A trailing underscore keeps a name off
    the keywords of Python (`class_`), an underscore inside one is a dash (`text_anchor`).
    """
    pairs = []
    for key, setting in attributes.items():
        name = key.rstrip("_").replace("_", "-")
        pairs.append(f'{name}="{setting}"')
    joined = " ".join(pairs)
    if text is None:
        return f"<{tag} {joined}/>"
    return f"<{tag} {joined}>{text}</{tag}>"


class Drawing:
    """
    The elements of a picture in the order they are drawn, each over the ones before it.
    """

    def __init__(self) -> None:
        self.parts: list[str] = []

    def add(self, part: str) -> None:
        self.parts.append(part)

    def rect(self, **attributes: object) -> None:
        self.add(element("rect", **attributes))

    def text(self, text: str, **attributes: object) -> None:
        self.add(element("text", text, **attributes))

    def edge(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        """
        A curve from the point `start` to the point `end`, level at both ends.
        """
        middle = (start[0] + end[0]) / 2
        curve = _CURVE.format(start=start, end=end, middle=middle)
        self.add(element("path", class_="edge", d=curve))

    def svg(self) -> str:
        """
        The picture, one element a line.
        """
        return "{}\n".format("\n".join(self.parts))
