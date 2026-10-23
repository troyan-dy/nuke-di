"""
What a coding agent reads stays in step with the docs: llms.txt and llms-full.txt are what scripts/llms.py makes of
the pages, the Agent Skill links to files and headings that exist, and the plugin manifests name the skill.
"""

import json
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

from tests.sequences import single
from tests.test_docs import anchors, split

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SKILL = ROOT / "skills" / "nuke-di" / "SKILL.md"


@contextmanager
def importable(directory: Path) -> Iterator[None]:
    """
    The modules of `directory` importable for the block, as they are when a script there runs.
    """
    sys.path.insert(0, str(directory))
    try:
        yield
    finally:
        sys.path.remove(str(directory))


def load() -> ModuleType:
    spec = spec_from_file_location("llms_script", SCRIPTS / "llms.py")
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    # scripts/llms.py imports its sibling scripts/llms_pages.py
    with importable(SCRIPTS):
        spec.loader.exec_module(module)
    return module


llms = load()
BLOB_URL, RAW_URL = llms.BLOB_URL, llms.RAW_URL


@pytest.mark.parametrize("name", ["llms.txt", "llms-full.txt"])
def test_generated_files_are_up_to_date(name: str) -> None:
    generated = llms.outputs()[name]
    assert (ROOT / name).read_text(encoding="utf-8") == generated, (
        f"{name} is out of date: run `uv run python scripts/llms.py`"
    )


def test_every_guide_page_is_in_the_full_text() -> None:
    pages = (ROOT / "docs" / "guide").glob("*.md")
    assert sorted(llms.GUIDE) == sorted(path.stem for path in pages)


def repository_files(text: str) -> list[tuple[str, str]]:
    """Every link to a file of this repository on GitHub: the path and the anchor."""
    prefixes = f"{re.escape(BLOB_URL)}|{re.escape(RAW_URL)}"
    found = re.findall(rf"(?:{prefixes})([^)#\s>`,]+)(?:#([^)\s>`,]+))?", text)
    return [(path, anchor) for path, anchor in found]


@pytest.mark.parametrize("path", ["llms.txt", "llms-full.txt", "skills/nuke-di/SKILL.md"])
def test_links_land_on_files_and_headings(path: str) -> None:
    prose, _ = split((ROOT / path).read_text(encoding="utf-8"))
    targets = repository_files(prose)

    assert targets
    for target, anchor in targets:
        assert (ROOT / target).is_file(), target
        if anchor and target.endswith(".md"):
            headings = anchors((ROOT / target).read_text(encoding="utf-8"))
            assert anchor in headings, f"{target}#{anchor}"


def test_full_text_has_no_relative_links() -> None:
    # Pages are concatenated, so a link relative to one of them would lead nowhere
    prose, _ = split((ROOT / "llms-full.txt").read_text(encoding="utf-8"))

    assert not re.findall(r"\]\((?![a-z]+:)([^)\s]+)\)", prose)


def test_full_text_drops_the_navigation() -> None:
    text = (ROOT / "llms-full.txt").read_text(encoding="utf-8")

    assert "← [Documentation]" not in text
    assert "img.shields.io" not in text


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match, "SKILL.md starts with a frontmatter block"
    lines = match.group(1).splitlines()
    return dict(line.split(": ", 1) for line in lines)


def test_skill_frontmatter_follows_the_agent_skills_format() -> None:
    # https://agentskills.io/specification: the name is the directory, the description at most 1024 characters
    fields = frontmatter(SKILL.read_text(encoding="utf-8"))

    assert fields["name"] == SKILL.parent.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fields["name"])
    assert 0 < len(fields["description"]) <= 1024
    assert "nuke-di" in fields["description"]
    assert "nuke_di" in fields["description"]


def test_skill_names_every_rejected_design() -> None:
    text = SKILL.read_text(encoding="utf-8")

    for adr in ("0005", "0006", "0008", "0009"):
        assert f"docs/adr/{adr}-" in text, adr


def test_plugin_marketplace_serves_the_skill() -> None:
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))

    # `/plugin install nuke-di@nuke-di`, as docs/guide/agents.md says
    assert marketplace["name"] == "nuke-di"
    entry = single(marketplace["plugins"])
    assert entry["name"] == plugin["name"] == "nuke-di"
    # The repository root is the plugin, and skills/ at the root is where Claude Code looks for its skills
    assert entry["source"] == "./"
    assert SKILL.is_file()
    assert "/plugin install nuke-di@nuke-di" in (ROOT / "docs" / "guide" / "agents.md").read_text(encoding="utf-8")


def test_context7_indexes_existing_folders() -> None:
    config = json.loads((ROOT / "context7.json").read_text(encoding="utf-8"))

    for folder in config["folders"]:
        assert (ROOT / folder).is_dir(), folder
    assert len(config["description"]) <= 500
