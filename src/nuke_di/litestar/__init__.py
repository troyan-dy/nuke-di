"""
Litestar integration: route handlers and their dependencies take clients by type hint.

See docs/specs/litestar.md and docs/adr/0004-litestar-clients-by-name.md.
"""

from nuke_di.litestar.plugin import ClientPlugin

__all__ = ("ClientPlugin",)
