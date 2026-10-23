"""
The pages of the documentation as scripts/llms.py reads them: their lines without what only a reader on GitHub
needs, their links absolute, and the descriptions the README gives the pages of the guide.
"""

import posixpath
import re
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Final

ROOT: Final = Path(__file__).resolve().parent.parent
BLOB_URL: Final = "https://github.com/troyan-dy/nuke-di/blob/master/"
RAW_URL: Final = "https://raw.githubusercontent.com/troyan-dy/nuke-di/master/"
README: Final = "README.md"
# A link of the README to a page of the guide
_GUIDE_LINK: Final = re.compile(rf"\]\({re.escape(BLOB_URL)}docs/guide/([\w-]+)\.md\)")
# A Markdown link: its target, and its text and target
_LINK: Final = re.compile(r"\]\(([^)\s]+)\)")
_LINK_TEXT: Final = re.compile(r"\[([^\]]+)\]\([^)]+\)")
# An entry of a list that links a page and says what it is
_DESCRIBED: Final = re.compile(r"- \[[^\]]+\]\([^)]+\): (.+)")
_ENTRY: Final = re.compile(r"\n(?=- )")


def page_text(source: str) -> str:
    return (ROOT / source).read_text(encoding="utf-8")


def absolute(target: str, source: str) -> str:
    """A link of `source` as an absolute URL: GitHub resolves a relative one against the file that holds it."""
    if re.match(r"[a-z]+:", target):
        return target
    path, hash_, anchor = target.partition("#")
    if not path:
        return f"{BLOB_URL}{source}#{anchor}"
    resolved = posixpath.normpath(PurePosixPath(source).parent / path)
    # A link to the root of the repository: normpath() calls it "."
    resolved = "" if resolved == "." else resolved
    return f"{BLOB_URL}{resolved}{hash_}{anchor}"


def _absolute_link(source: str, match: re.Match[str]) -> str:
    target = absolute(match.group(1), source)
    return f"]({target})"


def without_navigation(source: str, text: str) -> list[str]:
    """
    The lines of a page without what only a reader on GitHub needs: the badges of the README and the way back to
    the README of a guide page.
    """
    lines = text.splitlines()
    if source != README:
        # The title, then the way back to the README and the blank line under it (tests/test_docs.py keeps them there)
        return [*lines[:2], *lines[4:]]
    # The title, then the badges and the blank lines around them up to the pitch
    for number, line in enumerate(lines):
        if number and line[:1] not in {"", "["}:
            return [lines[0], "", *lines[number:]]
    raise LookupError("the README has no pitch after its badges")


def full_page(source: str) -> str:
    """A page for llms-full.txt: no navigation, every link outside the code blocks absolute."""
    lines: list[str] = []
    fenced = False
    link = partial(_absolute_link, source)
    for line in without_navigation(source, page_text(source)):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            lines.append(line)
        elif fenced:
            lines.append(line)
        else:
            lines.append(_LINK.sub(link, line))
    # One blank line between pages, whatever a page ends with
    while lines and not lines[-1]:
        lines.pop()
    return "{}\n".format("\n".join(lines))


def documentation() -> dict[str, str]:
    """
    The description of each guide page in the README "Documentation" list, by page: the text after the link of an
    entry that links one page, nothing for a page that shares its entry with others.
    """
    after = page_text(README).partition("## Documentation\n")[2]
    descriptions: dict[str, str] = {}
    for entry in _ENTRY.split(after.partition("\n## ")[0].strip()):
        descriptions.update(_descriptions(entry))
    return descriptions


def _descriptions(entry: str) -> dict[str, str]:
    """
    The guide pages an entry of the README "Documentation" list links, each with the text after the link.
    """
    text = " ".join(line.strip() for line in entry.splitlines())
    linked = _GUIDE_LINK.findall(text)
    match = _DESCRIBED.match(text)
    if match is None or len(linked) != 1:
        # A page that shares its entry with others gets no description
        return dict.fromkeys(linked, "")
    # A link inside a description keeps only its text: an item of the index is one link
    return dict.fromkeys(linked, _LINK_TEXT.sub(r"\1", match.group(1)))
