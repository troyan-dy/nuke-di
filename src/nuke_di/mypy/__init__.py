"""
A mypy plugin that checks the client trees of a program while mypy checks its types.

Enable it in the mypy configuration, `plugins = ["nuke_di.mypy"]` under `[tool.mypy]` of `pyproject.toml`. At every
`resolve()`, `inject()`, `@job` and `@worker` it walks the `__init__` of every client the call would build and reports
what the container would raise, with the same message, so a broken tree fails `mypy` instead of the process.

Only what mypy keeps in its cache is read: a module checked from the cache has its classes and their types, not their
definitions, and a check must say the same with a cold cache and a warm one.
"""

from nuke_di.mypy.plugin import NukeDIPlugin, plugin

# The version mypy's plugin snapshot reads from this module: a digest of the whole package (nuke_di.mypy.snapshot)
from nuke_di.mypy.snapshot import version as __version__

__all__ = ("NukeDIPlugin", "__version__", "plugin")
