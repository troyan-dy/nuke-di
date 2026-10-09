import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://github.com/troyan-dy/nuke-di/blob/master/"
LANGUAGES = ["ru", "zh-CN", "es", "pt-BR", "ja", "pl"]
LABELS = {
    None: "English",
    "ru": "Русский",
    "zh-CN": "简体中文",
    "es": "Español",
    "pt-BR": "Português (Brasil)",
    "ja": "日本語",
    "pl": "Polski",
}
# The guide: docs/guide/<page>.md, translated in docs/i18n/<language>/<page>.md
PAGES = sorted(path.stem for path in (ROOT / "docs" / "guide").glob("*.md"))


def path(language: str | None = None, page: str | None = None) -> str:
    # Relative to the repository root, as in a link to the file on GitHub
    if page is not None:
        return f"docs/guide/{page}.md" if language is None else f"docs/i18n/{language}/{page}.md"
    return "README.md" if language is None else f"docs/i18n/README.{language}.md"


def readme(language: str | None = None, page: str | None = None) -> str:
    return (ROOT / path(language, page)).read_text(encoding="utf-8")


def guide(page: str) -> str:
    return readme(page=page)


DOCUMENTS = [None, *PAGES]


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


def headings(prose: str, page: str | None = None) -> list[str]:
    # A guide page starts with its own `# ` title; in a README `# nuke-di` has no translated anchor
    return re.findall(r"^#{1,} (.+)$" if page else r"^#{2,} (.+)$", prose, flags=re.MULTILINE)


def internal_links(prose: str) -> list[str]:
    return sorted(re.findall(r"\]\(#([^)]+)\)", prose))


def slug(heading: str) -> str:
    # GitHub's anchor for a heading: lowercase, punctuation dropped, spaces become hyphens
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(text: str, page: str | None = None) -> list[str]:
    # The anchor of a translated heading is its <a id="...">, of an English one its slug
    prose, _ = split(text)
    found = []
    for heading in headings(prose, page):
        match = re.match(r'<a id="([^"]+)"></a>', heading)
        found.append(match.group(1) if match else slug(heading))
    return found


def english_anchors(page: str | None = None) -> list[str]:
    prose, _ = split(readme(page=page))
    return [slug(heading) for heading in headings(prose, page)]


def test_guide_exists() -> None:
    assert "clients" in PAGES
    assert "testing" in PAGES


@pytest.mark.parametrize("page", DOCUMENTS)
def test_english_internal_links_resolve(page: str | None) -> None:
    prose, _ = split(readme(page=page))

    assert set(internal_links(prose)) - set(english_anchors(page)) == set()


@pytest.mark.parametrize("page", DOCUMENTS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_code_blocks_match_english(language: str, page: str | None) -> None:
    # Code and its output are not translated: a translation shows exactly the examples of the English page
    _, expected = split(readme(page=page))
    _, actual = split(readme(language, page))

    assert len(actual) == len(expected)
    for number, (english, translated) in enumerate(zip(expected, actual, strict=True), start=1):
        assert translated == english, f"code block {number} differs from {path(page=page)}"


@pytest.mark.parametrize("page", DOCUMENTS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_sections_match_english(language: str, page: str | None) -> None:
    # Every heading keeps the English anchor as <a id="...">, so a section missing from a translation fails
    prose, _ = split(readme(language, page))
    found = [re.match(r'<a id="([^"]+)"></a>', heading) for heading in headings(prose, page)]

    assert [anchor.group(1) if anchor else None for anchor in found] == english_anchors(page)


@pytest.mark.parametrize("page", DOCUMENTS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_internal_links_match_english(language: str, page: str | None) -> None:
    prose, _ = split(readme(language, page))
    english, _ = split(readme(page=page))

    assert internal_links(prose) == internal_links(english)


@pytest.mark.parametrize("language", [None, *LANGUAGES])
def test_language_switcher(language: str | None) -> None:
    # Right after the badges: the current language in bold, every other one linked, in the same order
    entries = [
        f"**{label}**" if other == language else f"[{label}]({BASE_URL}{path(other)})"
        for other, label in LABELS.items()
    ]

    assert readme(language).splitlines()[8] == " · ".join(entries)


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("language", [None, *LANGUAGES])
def test_guide_language_switcher(language: str | None, page: str) -> None:
    # Under the title of a guide page, relative, so it works on any branch
    here = (ROOT / path(language, page)).parent
    entries = [
        f"**{label}**" if other == language else f"[{label}]({os.path.relpath(ROOT / path(other, page), here)})"
        for other, label in LABELS.items()
    ]

    assert readme(language, page).splitlines()[2] == " · ".join(entries)


@pytest.mark.parametrize("page", DOCUMENTS)
@pytest.mark.parametrize("language", [None, *LANGUAGES])
def test_relative_links_point_to_existing_files(language: str | None, page: str | None) -> None:
    # A translation lives in docs/i18n/, so its links to repository files go up two levels
    document = ROOT / path(language, page)
    prose, _ = split(readme(language, page))
    targets = re.findall(r"\]\((?![a-z]+:|#)([^)\s]+)", prose)

    assert targets
    for target in targets:
        file, _, anchor = target.partition("#")
        resolved = (document.parent / file).resolve()
        assert resolved.is_file(), target
        if anchor and resolved.suffix == ".md":
            # A link to a section of another page lands on a heading of that page
            assert anchor in anchors(resolved.read_text(encoding="utf-8"), page="any"), target
