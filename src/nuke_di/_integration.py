"""
Deprecated: the integration kit is public now, import it from `nuke_di.integration`. This alias is removed in
the next minor version.
"""

import warnings

from nuke_di.integration import (
    Binding,
    DependsFramework,
    Framework,
    bind,
    client_of,
    running,
    unique,
    wrap_lifespan,
)

__all__ = ("Binding", "DependsFramework", "Framework", "bind", "client_of", "running", "unique", "wrap_lifespan")

warnings.warn(
    "nuke_di._integration is deprecated, import from nuke_di.integration instead",
    DeprecationWarning,
    stacklevel=2,
)
