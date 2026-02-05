"""Unified config registry for all resource types."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Type

from hush.core.loggings import LOGGER
from hush.core.utils.yaml_model import YamlModel


@dataclass
class ConfigEntry:
    """Entry containing config class and its factory."""
    config_class: Type[YamlModel]
    factory: Callable[[YamlModel], Any]


class ConfigRegistry:
    """Unified registry for all config types.

    Each category has exactly one config class registered.
    Resolution: category -> ConfigEntry (config_class + factory).

    Usage:
        # Register a config class with its factory
        REGISTRY.register(LLMConfig, LLMFactory.create)

        # Get config class by category
        config_class = REGISTRY.get_class("llm")

        # Create instance from config
        instance = REGISTRY.create(config)
    """

    _instance: Optional['ConfigRegistry'] = None

    def __init__(self):
        # category -> ConfigEntry (one per category)
        self._entries: Dict[str, ConfigEntry] = {}
        # class name -> ConfigEntry (for lookup by class name / backward compat)
        self._class_entries: Dict[str, ConfigEntry] = {}

    @classmethod
    def instance(cls) -> 'ConfigRegistry':
        """Get global singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        cls._instance = None

    def register(
        self,
        config_class: Type[YamlModel],
        factory: Callable[[YamlModel], Any]
    ) -> None:
        """Register config class with factory.

        Args:
            config_class: Config class with _category attribute
            factory: Callable to create resource instance from config
        """
        category = getattr(config_class, '_category', '_default')
        entry = ConfigEntry(config_class=config_class, factory=factory)

        # Check duplicate category
        existing = self._entries.get(category)
        if existing and existing.config_class != config_class:
            raise ValueError(
                f"Duplicate category '{category}': "
                f"{existing.config_class.__name__} vs {config_class.__name__}"
            )

        # Register by category
        self._entries[category] = entry

        # Also register by class name (for backward compat)
        self._class_entries[config_class.__name__] = entry

        LOGGER.debug("Registered: %s -> %s", category, config_class.__name__)

    def get_entry(
        self,
        category: str,
    ) -> Optional[ConfigEntry]:
        """Lookup ConfigEntry by category."""
        entry = self._entries.get(category)
        if entry:
            return entry

        # Fallback to class name
        return self._class_entries.get(category)

    def get_class(
        self,
        category: str,
    ) -> Optional[Type[YamlModel]]:
        """Get config class by category."""
        entry = self.get_entry(category)
        return entry.config_class if entry else None

    def get_factory(
        self,
        config_class: Type[YamlModel]
    ) -> Optional[Callable]:
        """Get factory for a config class."""
        entry = self._class_entries.get(config_class.__name__)
        return entry.factory if entry else None

    def create(self, config: YamlModel) -> Any:
        """Create resource instance from config.

        Args:
            config: Config object to create instance from

        Returns:
            Resource instance

        Raises:
            ValueError: If no factory registered for config type
        """
        config_type = type(config)

        # Try exact match first
        entry = self._class_entries.get(config_type.__name__)

        # If not found, try parent classes (for inheritance)
        if not entry:
            for parent_type in config_type.__mro__[1:]:
                entry = self._class_entries.get(parent_type.__name__)
                if entry:
                    break

        if not entry:
            raise ValueError(f"No factory registered for {config_type.__name__}")

        return entry.factory(config)

    def clear(self):
        """Clear all registrations."""
        self._entries.clear()
        self._class_entries.clear()

    def categories(self) -> list[str]:
        """List all registered categories."""
        return list(self._entries.keys())


# Global singleton
REGISTRY = ConfigRegistry.instance()
