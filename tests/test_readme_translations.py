import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://github.com/troyan-dy/nuke-di/blob/master/"
LANGUAGES = ["ru", "zh-CN", "es", "pt-BR", "ja", "pl"]


def path(language: str | None = None) -> str:
    # Relative to the repository root, as in a link to the file on GitHub
    return "README.md" if language is None else f"docs/i18n/README.{language}.md"


def readme(language: str | None = None) -> str:
    return (ROOT / path(language)).read_text(encoding="utf-8")


def code_blocks(text: str) -> list[str]:
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
    assert current is None, "unclosed code block"
    return blocks


def outside_code(text: str) -> str:
    return re.sub(r"^ *```.*?^ *```$", "", text, flags=re.MULTILINE | re.DOTALL)


@pytest.mark.parametrize("language", LANGUAGES)
def test_code_blocks_match_english(language: str) -> None:
    # Code and its output are not translated: a translation shows exactly the examples of README.md
    expected = code_blocks(readme())
    actual = code_blocks(readme(language))

    assert len(actual) == len(expected)
    for number, (english, translated) in enumerate(zip(expected, actual, strict=True), start=1):
        assert translated == english, f"code block {number} differs from README.md"


@pytest.mark.parametrize("language", LANGUAGES)
def test_internal_links_resolve(language: str) -> None:
    # Translated headings get other anchors, so a translation keeps the English ones as <a id="...">
    text = outside_code(readme(language))
    anchors = set(re.findall(r'<a id="([^"]+)"></a>', text))
    links = set(re.findall(r"\]\(#([^)]+)\)", text))

    assert links == set(re.findall(r"\]\(#([^)]+)\)", outside_code(readme())))
    assert links - anchors == set()


@pytest.mark.parametrize("language", [None, *LANGUAGES])
def test_language_switcher_links_every_other_readme(language: str | None) -> None:
    text = readme(language)
    others = [other for other in [None, *LANGUAGES] if other != language]

    for other in others:
        assert f"]({BASE_URL}{path(other)})" in text


@pytest.mark.parametrize("language", [None, *LANGUAGES])
def test_relative_links_point_to_existing_files(language: str | None) -> None:
    # A translation lives in docs/i18n/, so its links to repository files go up two levels
    readme_path = ROOT / path(language)
    targets = re.findall(r"\]\((?!https?://|#)([^)]+)\)", outside_code(readme(language)))

    assert targets
    for target in targets:
        assert (readme_path.parent / target).resolve().is_file(), target
