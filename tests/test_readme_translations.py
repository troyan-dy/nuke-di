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


def path(language: str | None = None) -> str:
    # Relative to the repository root, as in a link to the file on GitHub
    return "README.md" if language is None else f"docs/i18n/README.{language}.md"


def readme(language: str | None = None) -> str:
    return (ROOT / path(language)).read_text(encoding="utf-8")


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
    return re.findall(r"^#{2,} (.+)$", prose, flags=re.MULTILINE)


def internal_links(prose: str) -> list[str]:
    return sorted(re.findall(r"\]\(#([^)]+)\)", prose))


def slug(heading: str) -> str:
    # GitHub's anchor for a heading: lowercase, punctuation dropped, spaces become hyphens
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def english_anchors() -> list[str]:
    prose, _ = split(readme())
    return [slug(heading) for heading in headings(prose)]


def test_english_internal_links_resolve() -> None:
    prose, _ = split(readme())

    assert set(internal_links(prose)) - set(english_anchors()) == set()


@pytest.mark.parametrize("language", LANGUAGES)
def test_code_blocks_match_english(language: str) -> None:
    # Code and its output are not translated: a translation shows exactly the examples of README.md
    _, expected = split(readme())
    _, actual = split(readme(language))

    assert len(actual) == len(expected)
    for number, (english, translated) in enumerate(zip(expected, actual, strict=True), start=1):
        assert translated == english, f"code block {number} differs from README.md"


@pytest.mark.parametrize("language", LANGUAGES)
def test_sections_match_english(language: str) -> None:
    # Every heading keeps the English anchor as <a id="...">, so a section missing from a translation fails
    prose, _ = split(readme(language))
    anchors = [re.match(r'<a id="([^"]+)"></a>', heading) for heading in headings(prose)]

    assert [anchor.group(1) if anchor else None for anchor in anchors] == english_anchors()


@pytest.mark.parametrize("language", LANGUAGES)
def test_internal_links_match_english(language: str) -> None:
    prose, _ = split(readme(language))
    english, _ = split(readme())

    assert internal_links(prose) == internal_links(english)


@pytest.mark.parametrize("language", [None, *LANGUAGES])
def test_language_switcher(language: str | None) -> None:
    # Right after the badges: the current language in bold, every other one linked, in the same order
    entries = [
        f"**{label}**" if other == language else f"[{label}]({BASE_URL}{path(other)})"
        for other, label in LABELS.items()
    ]

    assert readme(language).splitlines()[8] == " · ".join(entries)


@pytest.mark.parametrize("language", [None, *LANGUAGES])
def test_relative_links_point_to_existing_files(language: str | None) -> None:
    # A translation lives in docs/i18n/, so its links to repository files go up two levels
    readme_path = ROOT / path(language)
    prose, _ = split(readme(language))
    targets = re.findall(r"\]\((?![a-z]+:|#)([^)#\s]+)", prose)

    assert targets
    for target in targets:
        assert (readme_path.parent / target).resolve().is_file(), target
