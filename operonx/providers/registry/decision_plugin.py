"""Decision resource plugin for ResourceHub.

Auto-registers the decision config classes and factory handlers with operonx.
"""

from operonx.core.registry import REGISTRY
from operonx.providers.decisions.config import DecisionConfig
from operonx.providers.decisions.factory import create_decision

_registered = False


def register():
    """Register decision config class and factory handler."""
    global _registered
    if _registered:
        return

    REGISTRY.register(DecisionConfig, create_decision)
    _registered = True


def is_registered() -> bool:
    """Check if plugin has been registered."""
    return _registered


# Auto-register on import
register()
