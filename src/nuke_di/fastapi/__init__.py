"""
FastAPI integration: path operations and their dependencies take clients by type hint.

See docs/specs/fastapi.md and docs/adr/0003-fastapi-signature-rewrite.md.
"""

from nuke_di.fastapi.app import setup
from nuke_di.fastapi.routes import ClientRoute, ClientRouter

__all__ = ("ClientRoute", "ClientRouter", "setup")
