"""ResourceHub - centralized registry with lazy loading and pluggable storage."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional

from hush.core.loggings import LOGGER
from hush.core.utils.yaml_model import YamlModel

from .config_registry import REGISTRY
from .shortcuts.health import HealthCheckResult
from .storage import ConfigStorage, YamlConfigStorage

# Type hints for IDE support
if TYPE_CHECKING:
    from hush.providers.auth.keycloak import KeycloakTokenProvider
    from hush.providers.embeddings.base import BaseEmbedding
    from hush.providers.llms.base import BaseLLM
    from hush.providers.rerankers.base import BaseReranker


@dataclass
class CacheEntry:
    """Cache entry with config and instance (lazy loaded)."""

    config: YamlModel
    instance: Any = None


class ResourceHub:
    """Centralized registry for managing application resources.

    Features:
    - Lazy loading: resources are initialized on first access
    - Pluggable storage: YAML, JSON, or custom backend
    - Extensible: external packages register their configs and factories

    Example:
        hub = ResourceHub.from_yaml("configs/resources.yaml")
        llm = hub.get("llm:gpt-4o")
        stt = hub.get("triton:stt")

        # Or use global hub
        from hush.core.registry import get_hub
        llm = get_hub().get("llm:gpt-4o")
    """

    _instance: ClassVar[Optional["ResourceHub"]] = None

    def __init__(self, storage: ConfigStorage):
        """Initialize hub with storage backend.

        Args:
            storage: Storage backend for configs
        """
        self._storage = storage
        self._cache: Dict[str, CacheEntry] = {}

    # ========================================================================
    # Factory Methods
    # ========================================================================

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ResourceHub":
        """Create hub with YAML file storage.

        Args:
            path: Path to YAML config file

        Returns:
            ResourceHub instance
        """
        storage = YamlConfigStorage(Path(path))
        return cls(storage)

    @classmethod
    def from_json(cls, path: str | Path) -> "ResourceHub":
        """Create hub with JSON file storage.

        Args:
            path: Path to JSON config file

        Returns:
            ResourceHub instance
        """
        from .storage import JsonConfigStorage

        storage = JsonConfigStorage(Path(path))
        return cls(storage)

    @classmethod
    def instance(cls) -> "ResourceHub":
        """Get singleton instance.

        Returns:
            Global ResourceHub singleton

        Raises:
            RuntimeError: If singleton not initialized
        """
        if cls._instance is None:
            raise RuntimeError(
                "ResourceHub singleton not initialized. Call ResourceHub.set_instance() first."
            )
        return cls._instance

    @classmethod
    def set_instance(cls, hub: "ResourceHub"):
        """Set global singleton instance.

        Args:
            hub: ResourceHub instance to use as singleton
        """
        cls._instance = hub

    # ========================================================================
    # Load Config (Lazy)
    # ========================================================================

    def _load_config(self, key: str) -> Optional[YamlModel]:
        """Load a config from storage (lazy, on demand)."""
        if key in self._cache:
            return self._cache[key].config

        config_data = self._storage.load_one(key)
        if not config_data:
            return None

        # Extract category from key prefix: "llm:gpt-4" -> "llm"
        category = key.split(":")[0] if ":" in key else None
        if not category:
            LOGGER.warning("Invalid key format, missing category: %s", key)
            return None

        # Lookup config class by category
        config_class = REGISTRY.get_class(category)

        # Fall back to 'type' or '_class' field
        if not config_class:
            config_type = config_data.get("type") or config_data.get("_class")
            if config_type:
                config_class = REGISTRY.get_class(config_type)

        # No config class found — store raw dict as config (for triton, custom categories)
        if not config_class:
            self._cache[key] = CacheEntry(config=config_data)
            return config_data

        try:
            # Parse config (exclude type and _class fields)
            data = {k: v for k, v in config_data.items() if k not in ("type", "_class")}

            # If config class has create_config, use it to dispatch to subclass
            if hasattr(config_class, "create_config"):
                config = config_class.create_config(data)
            else:
                config = config_class.model_validate(data)

            self._cache[key] = CacheEntry(config=config)
            return config
        except Exception as e:
            LOGGER.error("Cannot parse config '%s': %s", key, e)
            return None

    def _hash_of(self, config: YamlModel) -> str:
        """Create MD5 hash of config for unique identification."""
        return hashlib.md5(config.model_dump_json().encode()).hexdigest()[:8]

    def _key_of(self, config: YamlModel) -> str:
        """Create registry key from config category and model/name/hash."""
        config_type = type(config)

        # Use _category if available, otherwise derive from class name
        if hasattr(config_type, "_category"):
            category = getattr(config_type, "_category")
        else:
            category = config_type.__name__.replace("Config", "").lower()

        # Resource based on model uses model name
        if hasattr(config, "model") and config.model:
            return f"{category}:{config.model}"

        # Resource based on name
        if hasattr(config, "name") and config.name:
            return f"{category}:{config.name}"

        # Fallback to hash
        return f"{category}:{self._hash_of(config)}"

    # ========================================================================
    # Public API
    # ========================================================================

    def keys(self) -> List[str]:
        """Return all registered keys (loads all configs from storage)."""
        all_configs = self._storage.load_all()

        for key, config_data in all_configs.items():
            if key not in self._cache:
                # Use _load_config which handles category resolution
                self._load_config(key)

        return list(self._cache.keys())

    def has(self, key: str) -> bool:
        """Check if resource exists in registry."""
        if key in self._cache:
            return True
        # Try loading from storage
        return self._load_config(key) is not None

    def get(self, key: str) -> Any:
        """Get resource instance by key (lazy load on first access).

        Args:
            key: Registry key of resource

        Returns:
            Initialized resource instance

        Raises:
            KeyError: If key not found or resource failed to initialize
        """
        # Return cached instance if available
        if key in self._cache and self._cache[key].instance is not None:
            instance = self._cache[key].instance
            # Refresh keycloak token if applicable (LLM resources)
            self._refresh_keycloak(instance)
            return instance

        # Load config from storage
        config = self._load_config(key)
        if not config:
            raise KeyError(f"Resource '{key}' not found in registry")

        # Resolve keycloak token if configured (api_key: "keycloak:xxx")
        resolved_config = self._resolve_keycloak(config)
        create_config = resolved_config or config

        # Lazy initialize resource
        try:
            instance = REGISTRY.create(create_config)
        except Exception as e:
            LOGGER.warning("Failed to create resource '%s': %s", key, e)
            raise KeyError(f"Resource '{key}' failed to initialize: {e}") from e

        if instance is None:
            raise KeyError(f"Cannot create resource for '{key}': factory returned None")

        # Attach keycloak provider for future token refresh
        if resolved_config is not None:
            keycloak_name = config.api_key[9:]
            instance._keycloak_provider = self.keycloak(keycloak_name)
            instance._original_config = config

        self._cache[key].instance = instance
        LOGGER.debug("Lazy loaded resource: %s", key)

        return self._cache[key].instance

    def get_config(self, key: str) -> YamlModel:
        """Get config object of resource.

        Args:
            key: Registry key

        Returns:
            Resource config

        Raises:
            KeyError: If key not found
        """
        config = self._load_config(key)
        if not config:
            raise KeyError(f"Config '{key}' not found in registry")
        return config

    def register(self, config: YamlModel, registry_key: Optional[str] = None) -> str:
        """Register new resource config.

        Args:
            config: Resource config object
            registry_key: Custom key (auto-generated if not provided)

        Returns:
            Registry key used
        """
        if not registry_key:
            registry_key = self._key_of(config)

        # Create instance immediately
        instance = REGISTRY.create(config)
        self._cache[registry_key] = CacheEntry(config=config, instance=instance)

        # Persist to storage (no type field needed - category is in the key)
        config_dict = json.loads(config.model_dump_json(exclude_none=True))
        self._storage.save(registry_key, config_dict)

        LOGGER.debug("Registered: %s", registry_key)
        return registry_key

    def remove(self, key: str) -> bool:
        """Remove resource from registry.

        Args:
            key: Registry key to remove

        Returns:
            True if removed, False if not found
        """
        if key not in self._cache:
            # Try loading first
            if not self._load_config(key):
                return False

        if key in self._cache:
            del self._cache[key]

        self._storage.remove(key)
        LOGGER.debug("Removed: %s", key)
        return True

    def clear(self):
        """Clear all resources from registry and storage."""
        keys = list(self._cache.keys())
        self._cache.clear()
        for key in keys:
            self._storage.remove(key)
        LOGGER.debug("Cleared all resources")

    def close(self):
        """Close storage connection and cleanup."""
        if self._storage:
            self._storage.close()

    # ========================================================================
    # Internal helpers (keycloak token resolution)
    # ========================================================================

    def _refresh_keycloak(self, instance) -> None:
        """Refresh keycloak token on a cached instance (if applicable)."""
        if not hasattr(instance, "_keycloak_provider"):
            return
        fresh_token = instance._keycloak_provider.get_token()
        if hasattr(instance, "client"):
            instance.client.api_key = fresh_token
        if hasattr(instance, "config"):
            instance.config.api_key = fresh_token

    def _resolve_keycloak(self, config: YamlModel) -> Optional[YamlModel]:
        """If config has keycloak:xxx api_key, resolve token. Returns None if not keycloak."""
        if not (hasattr(config, "api_key") and isinstance(config.api_key, str)):
            return None
        if not config.api_key.startswith("keycloak:"):
            return None

        keycloak_name = config.api_key[9:]
        try:
            resolved_token = self._resolve_api_key(config.api_key)
        except Exception as e:
            raise KeyError(f"keycloak '{keycloak_name}' failed ({type(e).__name__}: {e})") from e

        config_dict = config.model_dump()
        config_dict["api_key"] = resolved_token
        return type(config).model_validate(config_dict)

    def keycloak(self, key: str) -> "KeycloakTokenProvider":
        """Get KeycloakTokenProvider by key.

        Args:
            key: Keycloak config identifier (e.g., 'myapp')

        Returns:
            KeycloakTokenProvider instance with get_token() method
        """
        return self._get_with_prefix(key, "keycloak")

    # ========================================================================
    # API Key Resolution (for keycloak references)
    # ========================================================================

    def _resolve_api_key(self, api_key: str) -> str:
        """Resolve api_key value, handling keycloak references.

        If api_key starts with 'keycloak:', fetch token from the referenced
        KeycloakTokenProvider.

        Args:
            api_key: Either a static key or 'keycloak:<name>' reference

        Returns:
            Resolved API key string

        Example:
            # Static key - returned as-is
            _resolve_api_key("sk-xxx") -> "sk-xxx"

            # Keycloak reference - fetches token
            _resolve_api_key("keycloak:myapp") -> "eyJ..." (actual token)
        """
        if api_key.startswith("keycloak:"):
            keycloak_name = api_key[9:]  # Remove "keycloak:" prefix
            provider = self.keycloak(keycloak_name)
            return provider.get_token()
        return api_key

    # ========================================================================
    # Health Check
    # ========================================================================

    def health_check(self, keys: Optional[List[str]] = None) -> HealthCheckResult:
        """Check health of all or specified resources.

        Args:
            keys: Optional list of keys to check. If None, checks all.

        Returns:
            HealthCheckResult with status of each resource

        Example:
            result = hub.health_check()
            if not result.healthy:
                print(f"Unhealthy resources: {result.failed}")
        """
        check_keys = keys if keys else self.keys()
        results: Dict[str, bool] = {}
        errors: Dict[str, str] = {}

        for key in check_keys:
            try:
                # Try to load the resource
                self.get(key)
                results[key] = True
            except Exception as e:
                results[key] = False
                errors[key] = str(e)
                LOGGER.warning("Health check failed for '%s': %s", key, e)

        return HealthCheckResult(
            results=results,
            errors=errors,
        )
