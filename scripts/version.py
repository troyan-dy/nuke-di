"""
Every change that reaches master is a release, so it carries its own version.

    python scripts/version.py check BASE   fail unless the version is above the one on BASE, not released
                                            yet, and has its section and compare link in CHANGELOG.md
    python scripts/version.py unreleased   fail when the version is released already; print it otherwise
    python scripts/version.py notes        print the CHANGELOG.md section of the version

Only the standard library: CI runs it before installing anything.
"""

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_UNREADABLE = "cannot read pyproject.toml on {base}: {reason}"


def parse(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"{version!r} is not MAJOR.MINOR.PATCH")
    return tuple(map(int, version.split(".")))


def section(changelog: str, version: str) -> str | None:
    """
    The body of the `## [version] - date` section, up to the next section or the links.
    """
    heading = rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}\n"
    match = re.search(rf"{heading}(.*?)(?=^## \[|^\[Unreleased\]: )", changelog, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else None


def problems(version: str, base: str, changelog: str, *, tagged: bool) -> list[str]:
    """
    What keeps `version` from being released after `base`.
    """
    found = []
    if parse(version) <= parse(base):
        found.append(f"version {version} is not above {base} on master: bump it, e.g. `uv version --bump minor`")
    if tagged:
        found.append(f"v{version} is released already")
    if section(changelog, version) is None:
        found.append(f"CHANGELOG.md has no '## [{version}] - YYYY-MM-DD' section")
    elif f"\n[{version}]: " not in changelog:
        found.append(f"CHANGELOG.md has no compare link '[{version}]: ...'")
    return found


def _version_in(pyproject: str) -> str:
    return str(tomllib.loads(pyproject)["project"]["version"])


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    # git from PATH with fixed subcommands, run by CI and by `make check-version`
    command = ["git", *args]
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)  # noqa: S603


def _fail(errors: list[str]) -> int:
    for error in errors:
        # An annotation on the run page on GitHub Actions, a plain line elsewhere
        prefix = "::error::" if os.environ.get("GITHUB_ACTIONS") else "error: "
        sys.stderr.write(f"{prefix}{error}\n")
    return 1


def main(argv: list[str]) -> int:  # pragma: no cover - exercised by CI
    version = _version_in((ROOT / "pyproject.toml").read_text())
    changelog = (ROOT / "CHANGELOG.md").read_text()

    match argv:
        case ["check", base]:
            shown = _git("show", f"{base}:pyproject.toml")
            if shown.returncode != 0:
                return _fail([_UNREADABLE.format(base=base, reason=shown.stderr.strip())])
            tagged = _git("rev-parse", "--quiet", "--verify", f"refs/tags/v{version}").returncode == 0
            errors = problems(version, _version_in(shown.stdout), changelog, tagged=tagged)
        case ["unreleased"]:
            # Only `check` and `unreleased` ask git for the tag: `notes` and the usage run without git
            tagged = _git("rev-parse", "--quiet", "--verify", f"refs/tags/v{version}").returncode == 0
            errors = (
                [f"v{version} is released already: a change reached master without a version bump"] if tagged else []
            )
        case ["notes"]:
            sys.stdout.write("{}\n".format(section(changelog, version) or ""))
            return 0
        case _:
            sys.stderr.write(f"{__doc__}\n")
            return 2

    if errors:
        return _fail(errors)
    sys.stdout.write(f"{version}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
