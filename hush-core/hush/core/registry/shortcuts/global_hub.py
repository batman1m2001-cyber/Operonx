"""Global hub singleton utilities."""

import os
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from hush.core.loggings import LOGGER

if TYPE_CHECKING:
    from ..resource_hub import ResourceHub


# Global hub instance
_GLOBAL_HUB: Optional['ResourceHub'] = None


def _get_global_hub() -> Optional['ResourceHub']:
    """Get or create global ResourceHub instance.

    Tries to load config from:
    1. HUSH_CONFIG environment variable
    2. ./resources.yaml (current directory)
    3. ~/.hush/resources.yaml (home directory)

    Returns:
        Global ResourceHub instance, or None if initialization fails
    """
    global _GLOBAL_HUB

    if _GLOBAL_HUB is None:
        try:
            # Import here to avoid circular imports
            from ..resource_hub import ResourceHub

            config_path = None

            # 1. Check environment variable
            env_config = os.getenv('HUSH_CONFIG')
            if env_config and Path(env_config).exists():
                config_path = Path(env_config)

            # 2. Check current directory
            elif Path('resources.yaml').exists():
                config_path = Path('resources.yaml')

            # 3. Check home directory
            elif (Path.home() / '.hush' / 'resources.yaml').exists():
                config_path = Path.home() / '.hush' / 'resources.yaml'

            # Create hub
            if config_path:
                _GLOBAL_HUB = ResourceHub.from_yaml(config_path)
            else:
                # No config file found, create with default path
                _GLOBAL_HUB = ResourceHub.from_yaml(
                    Path.home() / '.hush' / 'resources.yaml'
                )

        except Exception as e:
            LOGGER.error("Cannot initialize global hub: %s", e)
            return None

    return _GLOBAL_HUB


def get_hub() -> 'ResourceHub':
    """Get global ResourceHub instance.

    This is the primary way to access the global hub.

    Returns:
        Global ResourceHub instance

    Raises:
        RuntimeError: If hub initialization fails

    Example:
        from hush.core.registry import get_hub

        hub = get_hub()
        llm = hub.llm("gpt-4")
    """
    hub = _get_global_hub()
    if hub is None:
        raise RuntimeError("Cannot initialize global ResourceHub")
    return hub


def set_global_hub(hub: 'ResourceHub'):
    """Set custom global ResourceHub instance.

    Use to override the default global hub.

    Args:
        hub: ResourceHub instance to use as global

    Example:
        from hush.core.registry import ResourceHub, set_global_hub

        custom_hub = ResourceHub.from_yaml("my_config.yaml")
        set_global_hub(custom_hub)
    """
    global _GLOBAL_HUB
    _GLOBAL_HUB = hub


def reset_global_hub():
    """Reset global hub (for testing)."""
    global _GLOBAL_HUB
    _GLOBAL_HUB = None
