"""Global hub singleton utilities."""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..resource_hub import ResourceHub


# Global hub instance — only set via set_global_hub() or Hush(resources=...)
_GLOBAL_HUB: Optional["ResourceHub"] = None


def _get_global_hub() -> Optional["ResourceHub"]:
    """Get global ResourceHub instance. Returns None if not initialized."""
    return _GLOBAL_HUB


def get_hub() -> "ResourceHub":
    """Get global ResourceHub instance.

    Returns:
        Global ResourceHub instance

    Raises:
        RuntimeError: If hub not initialized (call Hush(resources=...) first)

    Example:
        from hush.core.registry import get_hub

        hub = get_hub()
        llm = hub.llm("gpt-4")
    """
    hub = _get_global_hub()
    if hub is None:
        raise RuntimeError(
            "ResourceHub not initialized. Make sure to create a Hush engine "
            "with env and resources before running workflows.\n\n"
            "Example:\n"
            "  from hush.core import Hush\n"
            "  engine = Hush(workflow, env='.env', resources='resources.yaml')\n"
            "  result = await engine.run(inputs={...})\n\n"
            "Or initialize manually:\n"
            "  from dotenv import load_dotenv\n"
            "  load_dotenv('.env')  # load API keys first\n"
            "  from hush.core.registry import ResourceHub, set_global_hub\n"
            "  set_global_hub(ResourceHub.from_yaml('resources.yaml'))"
        )
    return hub


def set_global_hub(hub: "ResourceHub"):
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
