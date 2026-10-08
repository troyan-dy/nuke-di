import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def load() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "version.py"
    spec = importlib.util.spec_from_file_location("version_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


version = load()

CHANGELOG = """# Changelog

## [Unreleased]

## [1.6.0] - 2026-10-09

### Added

- Something new.

## [1.5.0] - 2026-10-08

### Added

- Something old.

[Unreleased]: https://github.com/troyan-dy/nuke-di/compare/v1.6.0...HEAD
[1.6.0]: https://github.com/troyan-dy/nuke-di/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/troyan-dy/nuke-di/compare/v1.4.0...v1.5.0
"""


def test_bumped_version_has_no_problems() -> None:
    assert version.problems("1.6.0", "1.5.0", CHANGELOG, tagged=False) == []


@pytest.mark.parametrize("base", ["1.6.0", "1.10.0", "2.0.0"])
def test_version_not_above_master(base: str) -> None:
    assert version.problems("1.6.0", base, CHANGELOG, tagged=False) == [
        f"version 1.6.0 is not above {base} on master: bump it, e.g. `uv version --bump minor`"
    ]


def test_released_version() -> None:
    assert version.problems("1.6.0", "1.5.0", CHANGELOG, tagged=True) == ["v1.6.0 is released already"]


def test_changelog_without_section() -> None:
    assert version.problems("1.7.0", "1.6.0", CHANGELOG, tagged=False) == [
        "CHANGELOG.md has no '## [1.7.0] - YYYY-MM-DD' section"
    ]


def test_changelog_without_compare_link() -> None:
    changelog = CHANGELOG.replace("[1.6.0]: https://github.com/troyan-dy/nuke-di/compare/v1.5.0...v1.6.0\n", "")

    assert version.problems("1.6.0", "1.5.0", changelog, tagged=False) == [
        "CHANGELOG.md has no compare link '[1.6.0]: ...'"
    ]


def test_notes_are_the_section_body() -> None:
    assert version.section(CHANGELOG, "1.6.0") == "### Added\n\n- Something new."
    assert version.section(CHANGELOG, "1.5.0") == "### Added\n\n- Something old."


def test_version_must_be_semver() -> None:
    with pytest.raises(ValueError, match=r"'1\.6' is not MAJOR\.MINOR\.PATCH"):
        version.problems("1.6", "1.5.0", CHANGELOG, tagged=False)
