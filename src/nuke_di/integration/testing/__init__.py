"""
The contract every framework integration keeps, as one call for the integration's own tests.

See docs/guide/integrations.md.
"""

from nuke_di.integration.testing.check import check
from nuke_di.integration.testing.outcomes import Send

__all__ = ("Send", "check")
