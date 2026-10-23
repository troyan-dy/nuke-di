"""
The documentation for coding agents, generated from the pages: llms.txt (https://llmstxt.org), an index
of the README and the guide, and llms-full.txt, the README and the guide in one file.

    python scripts/llms.py          write llms.txt and llms-full.txt
    python scripts/llms.py --check  fail when the checked-in files differ from what the pages give

Only the standard library. tests/test_llms.py fails when the files drift from the pages. How a page is read is
scripts/llms_pages.py.
"""

import sys
from pathlib import Path
from typing import Final

from llms_pages import BLOB_URL, RAW_URL, README, ROOT, documentation, full_page, page_text, without_navigation

# The guide in reading order; tests/test_llms.py fails when a page of docs/guide/ is missing here
GUIDE: Final = (
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
)

INTRO: Final = """\
Before writing code with nuke-di, know what it deliberately lacks. A dependency is a `Client` subclass with a
type-hinted `__init__` and async `connect()` / `disconnect()`; the type hint is the only registration. There are no
provider or factory functions (ADR-0005), no binding of a Protocol to an implementation, qualifiers or multibinding
(ADR-0008), no per-request or per-message scopes (ADR-0006) and no retries in `connect()` (ADR-0009): the
recipes in the guide reach the same results with plain clients, `mock()` and `override()`."""

# Every source, relative to the root: the README, then the guide
SOURCES: Final = (README, *(f"docs/guide/{page}.md" for page in GUIDE))
# The entries of the index beyond the documentation: the title, the path and what it is
OPTIONAL: Final = (
    ("The whole documentation in one file", "llms-full.txt", "the README and the guide above, concatenated"),
    ("Agent Skill", "skills/nuke-di/SKILL.md", "the model, the recipes and what not to write, on one page"),
    ("Examples", "examples/README.md", "runnable applications, each with its output and tests"),
    ("Changelog", "CHANGELOG.md", ""),
)


def full() -> str:
    header = (
        "# nuke-di: the full documentation\n\n"
        "The README and every page of the guide, generated from the pages by scripts/llms.py.\n"
        f"Index: {RAW_URL}llms.txt\n"
    )
    sections = [f"<!-- Source: {BLOB_URL}{source} -->\n\n{full_page(source)}" for source in SOURCES]
    return "\n".join([header, *sections])


def index() -> str:
    lines = without_navigation(README, page_text(README))
    # The first line of the pitch
    summary = next(filter(None, lines[1:]))
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
            *_docs(),
            "",
            "## Optional",
            "",
            *(_entry(*entry) for entry in OPTIONAL),
            "",
        ]
    )


def _docs() -> list[str]:
    """
    The entries of the README and of every page of the guide, with the description the README gives a page.
    """
    descriptions = documentation()
    docs = [_entry("README", README, "the pitch, the Quick start, the principles and two examples")]
    for source in SOURCES[1:]:
        title = page_text(source).splitlines()[0].removeprefix("# ")
        description = descriptions.get(Path(source).stem, "")
        docs.append(_entry(title, source, description))
    return docs


def _entry(title: str, path: str, description: str = "") -> str:
    link = f"- [{title}]({RAW_URL}{path})"
    return f"{link}: {description}" if description else link


def outputs() -> dict[str, str]:
    return {"llms.txt": index(), "llms-full.txt": full()}


def _stale(generated: dict[str, str]) -> list[str]:
    """The files that differ from what the pages give, or are missing."""
    stale = []
    for name, text in generated.items():
        path = ROOT / name
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            stale.append(name)
    return stale


def main(argv: list[str]) -> int:  # pragma: no cover - exercised by tests/test_llms.py through outputs()
    generated = outputs()
    if argv == ["--check"]:
        stale = _stale(generated)
        for name in stale:
            sys.stderr.write(f"error: {name} is out of date, run `python scripts/llms.py`\n")
        return 1 if stale else 0
    for name, text in generated.items():
        (ROOT / name).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
