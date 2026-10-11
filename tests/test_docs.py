"""
Every link of the README and the guide pages of docs/guide/ lands on a file and a heading that exist.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://github.com/troyan-dy/nuke-di/blob/master/"
RAW_URL = "https://raw.githubusercontent.com/troyan-dy/nuke-di/master/"
# The guide: docs/guide/<page>.md
PAGES = sorted(path.stem for path in (ROOT / "docs" / "guide").glob("*.md"))
# A document is the README (page None) or a guide page
DOCUMENTS = [None, *PAGES]


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
        elif current is not None:
            current.append(line)
        else:
            prose.append(line)
    assert current is None, "unclosed code block"
    return "\n".join(prose), blocks


def headings(prose: str) -> list[str]:
    # Every heading but `# nuke-di` of a README; a guide page's `# ` title is a section of its own
    return [heading for heading in re.findall(r"^#+ (.+)$", prose, flags=re.MULTILINE) if heading != "nuke-di"]


def internal_links(prose: str) -> list[str]:
    return sorted(re.findall(r"\]\(#([^)]+)\)", prose))


def relative_links(prose: str) -> list[str]:
    return re.findall(r"\]\((?![a-z]+:|#)([^)\s]+)", prose)


def slug(heading: str) -> str:
    # GitHub's anchor for a heading: lowercase, punctuation dropped, spaces become hyphens
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def explicit_anchor(heading: str) -> str | None:
    match = re.match(r'<a id="([^"]+)"></a>', heading)
    return match.group(1) if match else None


def anchors(text: str) -> list[str]:
    # A heading may carry its anchor as <a id="...">, any other one is its own slug
    prose, _ = split(text)
    return [explicit_anchor(heading) or slug(heading) for heading in headings(prose)]


def test_guide_exists() -> None:
    assert "clients" in PAGES
    assert "testing" in PAGES


@pytest.mark.parametrize("page", DOCUMENTS)
def test_english_internal_links_resolve(page: str | None) -> None:
    prose, _ = split(document(page))

    assert set(internal_links(prose)) - set(anchors(document(page))) == set()


@pytest.mark.parametrize("page", DOCUMENTS)
def test_relative_links_point_to_existing_files(page: str | None) -> None:
    # A link is relative to the file that holds it
    holder = ROOT / path(page)
    prose, _ = split(document(page))
    targets = relative_links(prose)

    assert targets
    for target in targets:
        file, _, anchor = target.partition("#")
        resolved = (holder.parent / file).resolve()
        assert resolved.is_file(), target
        if anchor and resolved.suffix == ".md":
            # A link to a section of another page lands on a heading of that page
            assert anchor in anchors(resolved.read_text(encoding="utf-8")), target


def test_readme_links_into_the_repository_are_absolute() -> None:
    # PyPI shows README.md too, where only an absolute link reaches the docs; every one names an existing file
    prose, _ = split(document())
    targets = re.findall(rf"\]\((?:{re.escape(BASE_URL)}|{re.escape(RAW_URL)})([^)#\s]+)", prose)

    assert any(target.startswith("docs/guide/") for target in targets)
    for target in targets:
        assert (ROOT / target).is_file(), target
    assert [target for target in relative_links(prose) if target.startswith("docs/")] == []
