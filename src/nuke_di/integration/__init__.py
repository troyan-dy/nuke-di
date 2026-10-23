"""
The kit the framework integrations are built on: client arguments filled by the framework's own DI, and the
container connected for the time an app runs. `nuke_di.fastapi`, `nuke_di.faststream`, `nuke_di.litestar`,
`nuke_di.aiogram`, `nuke_di.taskiq` and `nuke_di.asgi` are built on it, as are `nuke_di.mcp` and
`nuke_di.fastmcp`, and an integration with another framework needs nothing private of nuke-di besides it.

See docs/guide/integrations.md and docs/adr/0003-fastapi-signature-rewrite.md.
"""

from nuke_di.integration.binding import Binding, DependsFramework, Framework, client_of, unique
from nuke_di.integration.lifespan import running, wrap_lifespan
from nuke_di.integration.rewrite import bind

__all__ = ("Binding", "DependsFramework", "Framework", "bind", "client_of", "running", "unique", "wrap_lifespan")
