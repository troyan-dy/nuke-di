"""
aiogram integration: handlers take clients by type hint, and the dispatcher runs the container: the clients
connect before its startup handlers and disconnect after its shutdown handlers.

See docs/specs/aiogram.md.
"""

from nuke_di.aiogram.dispatcher import setup

__all__ = ("setup",)
