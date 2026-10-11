"""
The documentation for coding agents, generated from the pages: llms.txt (https://llmstxt.org), an index
of the README and the guide, and llms-full.txt, the README and the guide in one file.

    python scripts/llms.py          write llms.txt and llms-full.txt
    python scripts/llms.py --check  fail when the checked-in files differ from what the pages give

Only the standard library. tests/test_llms.py fails when the files drift from the pages.
"""

import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
BLOB_URL = "https://github.com/troyan-dy/nuke-di/blob/master/"
RAW_URL = "https://raw.githubusercontent.com/troyan-dy/nuke-di/master/"
# The guide in reading order; tests/test_llms.py fails when a page of docs/guide/ is missing here
GUIDE = [
    "clients",
    "container",
    "workers-and-jobs",
    "fastapi",
    "litestar",
    "faststream",
    "mcp",
    "aiogram",
    "taskiq",
    "asgi",
    "servers-in-workers",
    "integrations",
    "testing",
    "configuration",
    "errors",
    "agents",
    "development",
]

INTRO = """\
Before writing code with nuke-di, know what it deliberately lacks. A dependency is a `Client` subclass with a
type-hinted `__init__` and async `connect()` / `disconnect()`; the type hint is the only registration. There are no
provider or factory functions (ADR-0005), no binding of a Protocol to an implementation, qualifiers or multibinding
(ADR-0008), no per-request or per-message scopes (ADR-0006) and no retries in `connect()` (ADR-0009): the
recipes in the guide reach the same results with plain clients, `mock()` and `override()`."""


def pages() -> list[str]:
    """Every source, relative to the root: the README, then the guide."""
    return ["README.md", *(f"docs/guide/{page}.md" for page in GUIDE)]


def _read(source: str) -> str:
    return (ROOT / source).read_text(encoding="utf-8")


def _absolute(target: str, source: str) -> str:
    """A link of `source` as an absolute URL: GitHub resolves a relative one against the file that holds it."""
    if re.match(r"[a-z]+:", target):
        return target
    file, hash_, anchor = target.partition("#")
    if not file:
        return f"{BLOB_URL}{source}#{anchor}"
    parts: list[str] = []
    for part in (PurePosixPath(source).parent / file).parts:
        if part == "..":
            parts.pop()
        elif part != ".":
            parts.append(part)
    return f"{BLOB_URL}{'/'.join(parts)}{hash_}{anchor}"


def _without_navigation(source: str, text: str) -> list[str]:
    """
    The lines of a page without what only a reader on GitHub needs: the badges of the README and the way back to
    the README of a guide page.
    """
    lines = text.splitlines()
    if source == "README.md":
        # The title, then the badges and the blank lines around them up to the pitch
        start = next(number for number, line in enumerate(lines) if number > 0 and line and line[0] != "[")
        return [lines[0], "", *lines[start:]]
    # The title, then the way back to the README and the blank line under it (tests/test_docs.py keeps them there)
    return [*lines[:2], *lines[4:]]


def full_page(source: str) -> str:
    """A page for llms-full.txt: no navigation, every link outside the code blocks absolute."""
    lines: list[str] = []
    fenced = False
    for line in _without_navigation(source, _read(source)):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            line = re.sub(r"\]\(([^)\s]+)\)", lambda match: f"]({_absolute(match.group(1), source)})", line)
        lines.append(line)
    # One blank line between pages, whatever a page ends with
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def full() -> str:
    header = (
        "# nuke-di: the full documentation\n\n"
        "The README and every page of the guide, generated from the pages by scripts/llms.py.\n"
        f"Index: {RAW_URL}llms.txt\n"
    )
    sections = [f"<!-- Source: {BLOB_URL}{source} -->\n\n{full_page(source)}" for source in pages()]
    return "\n".join([header, *sections])


def _documentation() -> dict[str, str]:
    """
    The description of each guide page in the README "Documentation" list, by page: the text after the link of an
    item that links one page, nothing for a page that shares its item with others.
    """
    readme = _read("README.md")
    listing = readme.split("## Documentation\n", 1)[1].split("\n## ", 1)[0]
    items = re.split(r"\n(?=- )", listing.strip())
    descriptions: dict[str, str] = {}
    for item in items:
        text = " ".join(line.strip() for line in item.splitlines())
        linked = re.findall(r"\]\(" + re.escape(BLOB_URL) + r"docs/guide/([\w-]+)\.md\)", text)
        match = re.match(r"- \[[^\]]+\]\([^)]+\): (.+)", text)
        # A link inside a description keeps only its text: an item of the index is one link
        description = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", match.group(1)) if match else ""
        for page in linked:
            descriptions[page] = description if len(linked) == 1 else ""
    return descriptions


def _title(source: str) -> str:
    return _read(source).splitlines()[0].removeprefix("# ")


def index() -> str:
    # The first line of the pitch
    summary = next(line for line in _without_navigation("README.md", _read("README.md"))[1:] if line)
    descriptions = _documentation()

    def item(title: str, path: str, description: str = "") -> str:
        return f"- [{title}]({RAW_URL}{path})" + (f": {description}" if description else "")

    docs = [item("README", "README.md", "the pitch, the Quick start, the principles and two examples")]
    docs += [item(_title(source), source, descriptions.get(Path(source).stem, "")) for source in pages()[1:]]
    optional = [
        item("The whole documentation in one file", "llms-full.txt", "the README and the guide above, concatenated"),
        item("Agent Skill", "skills/nuke-di/SKILL.md", "the model, the recipes and what not to write, on one page"),
        item("Examples", "examples/README.md", "runnable applications, each with its output and tests"),
        item("Changelog", "CHANGELOG.md"),
    ]
    return "\n".join(
        [
            "# nuke-di",
            "",
            f"> {summary}",
            "",
            INTRO,
            "",
            "## Docs",
            "",
            *docs,
            "",
            "## Optional",
            "",
            *optional,
            "",
        ]
    )


def outputs() -> dict[str, str]:
    return {"llms.txt": index(), "llms-full.txt": full()}


def _current(name: str, text: str) -> bool:
    path = ROOT / name
    return path.is_file() and path.read_text(encoding="utf-8") == text


def main(argv: list[str]) -> int:  # pragma: no cover - exercised by tests/test_llms.py through outputs()
    if argv == ["--check"]:
        stale = [name for name, text in outputs().items() if not _current(name, text)]
        for name in stale:
            print(f"error: {name} is out of date, run `python scripts/llms.py`", file=sys.stderr)
        return 1 if stale else 0
    for name, text in outputs().items():
        (ROOT / name).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
