"""
FastMCP integration (`fastmcp` 4.x): the tools, resources and prompts of a `FastMCP` server, and the functions
they depend on, take clients by type hint.

See docs/specs/mcp.md and docs/adr/0011-mcp-clients-through-the-sdk-markers.md.
"""

from nuke_di.fastmcp.server import setup

__all__ = ("setup",)
