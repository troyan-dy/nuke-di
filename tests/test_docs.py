"""
Every link of the README and the guide pages of docs/guide/ lands on a file and a heading that exist.
"""

import re
from pathlib import Path
from typing import Final

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://github.com/troyan-dy/nuke-di/blob/master/"
RAW_URL = "https://raw.githubusercontent.com/troyan-dy/nuke-di/master/"
# The guide: docs/guide/<page>.md
GUIDE = ROOT / "docs" / "guide"
PAGES = sorted(guide_page.stem for guide_page in GUIDE.glob("*.md"))
# A document is the README (page None) or a guide page
DOCUMENTS: Final = (None, *PAGES)


def path(page: str | None = None) -> str:
    # Relative to the repository root, as in a link to the file on GitHub
    return "README.md" if page is None else f"docs/guide/{page}.md"


def document(page: str | None = None) -> str:
    return (ROOT / path(page)).read_text(encoding="utf-8")


def split(text: str) -> tuple[str, list[str]]:
    """The prose outside code blocks and the code blocks themselves."""
    prose: list[str] = []
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        # A fence may be indented, e.g. a code block inside a list item
        if line.lstrip().startswith("```"):
            if current is None:
                current = [line]
            else:
                current.append(line)
                blocks.append("\n".join(current))
                current = None
        elif current is None:
            prose.append(line)
        else:
            current.append(line)
    assert current is None, "unclosed code block"
    return "\n".join(prose), blocks


def headings(prose: str) -> list[str]:
    # Every heading but `# nuke-di` of a README; a guide page's `# ` title is a section of its own
    found = re.findall(r"^#+ (.+)$", prose, flags=re.MULTILINE)
    return [heading for heading in found if heading != "nuke-di"]


def internal_links(prose: str) -> list[str]:
    return sorted(re.findall(r"\]\(#([^)]+)\)", prose))


def relative_links(prose: str) -> list[str]:
    return re.findall(r"\]\((?![a-z]+:|#)([^)\s]+)", prose)


def slug(heading: str) -> str:
    # GitHub's anchor for a heading: lowercase, punctuation dropped, spaces become hyphens
    words = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return words.replace(" ", "-")


def explicit_anchor(heading: str) -> str | None:
    match = re.match(r'<a id="([^"]+)"></a>', heading)
    return match.group(1) if match else None


def anchors(text: str) -> list[str]:
    # A heading may carry its anchor as <a id="...">, any other one is its own slug
    prose, _ = split(text)
    return [explicit_anchor(heading) or slug(heading) for heading in headings(prose)]


def landing(page: str | None, target: str) -> tuple[Path, str]:
    """The file a relative link of `page` lands on, and the anchor it names there ("" for none)."""
    # A link is relative to the file that holds it
    holder = ROOT / path(page)
    filename, _, anchor = target.partition("#")
    return (holder.parent / filename).resolve(), anchor


def test_guide_exists() -> None:
    assert "clients" in PAGES
    assert "testing" in PAGES


@pytest.mark.parametrize("page", PAGES)
def test_guide_page_header(page: str) -> None:
    # The title, then the way back to the README, which scripts/llms.py drops by its place
    lines = document(page).splitlines()

    assert lines[0].startswith("# ")
    assert lines[1:4] == ["", "← [Documentation](../../README.md#documentation)", ""]


@pytest.mark.parametrize("page", DOCUMENTS)
def test_internal_links_resolve(page: str | None) -> None:
    prose, _ = split(document(page))
    known = set(anchors(document(page)))

    assert set(internal_links(prose)) - known == set()


@pytest.mark.parametrize("page", DOCUMENTS)
def test_relative_links_point_to_existing_files(page: str | None) -> None:
    prose, _ = split(document(page))
    targets = relative_links(prose)

    assert targets
    for target in targets:
        resolved, anchor = landing(page, target)
        assert resolved.is_file(), target
        if anchor and resolved.suffix == ".md":
            # A link to a section of another page lands on a heading of that page
            assert anchor in anchors(resolved.read_text(encoding="utf-8")), target


def test_readme_links_into_the_repository_are_absolute() -> None:
    # PyPI shows README.md too, where only an absolute link reaches the docs; every one names an existing file
    prose, _ = split(document())
    bases = "|".join(map(re.escape, (BASE_URL, RAW_URL)))
    targets = re.findall(rf"\]\((?:{bases})([^)#\s]+)", prose)

    assert any(target.startswith("docs/guide/") for target in targets)
    for target in targets:
        assert (ROOT / target).is_file(), target
    into_docs = [link for link in relative_links(prose) if link.startswith("docs/")]
    assert not into_docs
