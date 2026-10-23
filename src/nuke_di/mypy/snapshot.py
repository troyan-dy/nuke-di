"""
What mypy keys its cache on for this plugin.

mypy reads the plugin once per run and keeps its results in the cache only while the plugin is the same: it hashes
the file of the module `plugins` names, here `nuke_di/mypy/__init__.py`, and adds its `__version__`. That file only
re-exports, so the version is a digest of every module of the package: a change to any of them, in a release or in a
checkout, clears what a warm cache holds, as a change to the single-file plugin did.
"""

import hashlib
from pathlib import Path


def fingerprint(package: Path) -> str:
    """
    A digest of the names and the sources of the modules of `package`.
    """
    digest = hashlib.sha256()
    for module in sorted(package.glob("*.py")):
        digest.update(module.name.encode())
        digest.update(module.read_bytes())
    return digest.hexdigest()


version = fingerprint(Path(__file__).parent)
