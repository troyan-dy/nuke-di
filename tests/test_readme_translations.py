"""
The README, the guide pages of docs/guide/ and their translations stay in step: the same code blocks,
sections and links in every language, and every link lands on a file and a heading that exist.
"""

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://github.com/troyan-dy/nuke-di/blob/master/"
RAW_URL = "https://raw.githubusercontent.com/troyan-dy/nuke-di/master/"
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
# A document is the README (page None) or a guide page
DOCUMENTS = [None, *PAGES]


def path(language: str | None = None, page: str | None = None) -> str:
    # Relative to the repository root, as in a link to the file on GitHub
    if page is not None:
        return f"docs/guide/{page}.md" if language is None else f"docs/i18n/{language}/{page}.md"
    return "README.md" if language is None else f"docs/i18n/README.{language}.md"


def document(language: str | None = None, page: str | None = None) -> str:
    return (ROOT / path(language, page)).read_text(encoding="utf-8")


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


def translated_anchor(heading: str) -> str | None:
    match = re.match(r'<a id="([^"]+)"></a>', heading)
    return match.group(1) if match else None


def anchors(text: str) -> list[str]:
    # A translated heading carries its English anchor as <a id="...">, an English one is its own slug
    prose, _ = split(text)
    return [translated_anchor(heading) or slug(heading) for heading in headings(prose)]


def test_guide_exists() -> None:
    assert "clients" in PAGES
    assert "testing" in PAGES


@pytest.mark.parametrize("page", DOCUMENTS)
def test_english_internal_links_resolve(page: str | None) -> None:
    prose, _ = split(document(page=page))

    assert set(internal_links(prose)) - set(anchors(document(page=page))) == set()


@pytest.mark.parametrize("page", DOCUMENTS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_code_blocks_match_english(language: str, page: str | None) -> None:
    # Code and its output are not translated: a translation shows exactly the examples of the English page
    _, expected = split(document(page=page))
    _, actual = split(document(language, page))

    assert len(actual) == len(expected)
    for number, (english, translated) in enumerate(zip(expected, actual, strict=True), start=1):
        assert translated == english, f"code block {number} differs from {path(page=page)}"


@pytest.mark.parametrize("page", DOCUMENTS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_sections_match_english(language: str, page: str | None) -> None:
    # Every heading keeps the English anchor as <a id="...">, so a section missing from a translation fails
    prose, _ = split(document(language, page))

    assert [translated_anchor(heading) for heading in headings(prose)] == anchors(document(page=page))


@pytest.mark.parametrize("page", DOCUMENTS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_internal_links_match_english(language: str, page: str | None) -> None:
    prose, _ = split(document(language, page))
    english, _ = split(document(page=page))

    assert internal_links(prose) == internal_links(english)


@pytest.mark.parametrize("language", [None, *LANGUAGES])
def test_language_switcher(language: str | None) -> None:
    # Right after the badges: the current language in bold, every other one linked, in the same order
    entries = [
        f"**{label}**" if other == language else f"[{label}]({BASE_URL}{path(other)})"
        for other, label in LABELS.items()
    ]

    assert document(language).splitlines()[8] == " · ".join(entries)


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("language", [None, *LANGUAGES])
def test_guide_language_switcher(language: str | None, page: str) -> None:
    # Under the title of a guide page, relative, so it works on any branch
    here = (ROOT / path(language, page)).parent
    entries = [
        f"**{label}**" if other == language else f"[{label}]({os.path.relpath(ROOT / path(other, page), here)})"
        for other, label in LABELS.items()
    ]

    assert document(language, page).splitlines()[2] == " · ".join(entries)


@pytest.mark.parametrize("page", DOCUMENTS)
@pytest.mark.parametrize("language", [None, *LANGUAGES])
def test_relative_links_point_to_existing_files(language: str | None, page: str | None) -> None:
    # A link is relative to the file that holds it, which may sit one, two or three levels deep
    holder = ROOT / path(language, page)
    prose, _ = split(document(language, page))
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


@pytest.mark.parametrize("page", DOCUMENTS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_translations_link_to_translated_pages(language: str, page: str | None) -> None:
    # Below the language switcher, a translation leads to the pages of its own language, never the English ones
    lines = document(language, page).splitlines()
    prose, _ = split("\n".join(lines[3:] if page else lines[9:]))
    holder = ROOT / path(language, page)
    english = {(ROOT / path(page=other)).resolve() for other in DOCUMENTS}

    for target in relative_links(prose):
        assert (holder.parent / target.partition("#")[0]).resolve() not in english, target
