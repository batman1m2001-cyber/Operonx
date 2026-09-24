"""ResourceHub - centralized registry with lazy loading and pluggable storage."""

import hashlib
import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Tuple

from operonx.core.loggings import LOGGER
from operonx.core.utils.yaml_model import YamlModel

from .config_registry import REGISTRY
from .errors import EnvVarUnsetError, ResourceHubWarning, ResourceUnreachable
from .shortcuts.health import HealthCheckResult
from .storage import ConfigStorage, YamlConfigStorage

# Type hints for IDE support
if TYPE_CHECKING:
    from operonx.providers.auth.keycloak import KeycloakTokenProvider
    from operonx.providers.auth.oauth2 import OAuth2TokenProvider
    from operonx.providers.embeddings.base import BaseEmbedding
    from operonx.providers.llms.base import BaseLLM
    from operonx.providers.rerankers.base import BaseReranker


@dataclass
class CacheEntry:
    """Cache entry with config and instance (lazy loaded)."""

    config: YamlModel
    instance: Any = None


#: Resource categories whose instances expose ``get_token()``. An
#: ``api_key: "<category>:<name>"`` on any other resource is resolved by
#: fetching a bearer token from the named provider. Adding a category here
#: plus a ``REGISTRY.register`` is all a new auth scheme needs.
TOKEN_REF_PREFIXES = ("keycloak", "oauth2")


def _split_token_ref(api_key: Any) -> Optional[Tuple[str, str]]:
    """``"oauth2:databricks"`` -> ``("oauth2", "databricks")``, else None.

    Returns None for a static key, a non-string, or a prefix that is not a
    registered token provider — so ``"sk-proj:abc"`` stays a literal key
    rather than being mistaken for a reference.
    """
    if not isinstance(api_key, str):
        return None
    category, sep, name = api_key.partition(":")
    if not sep or not name or category not in TOKEN_REF_PREFIXES:
        return None
    return category, name


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
        from operonx.core.registry import ResourceHub
        llm = ResourceHub.instance().get("llm:gpt-4o")
    """

    _instance: ClassVar[Optional["ResourceHub"]] = None

    def __init__(self, storage: ConfigStorage, source_path: Optional[Path] = None):
        """Initialize hub with storage backend.

        Args:
            storage: Storage backend for configs
            source_path: Absolute path of the file the storage was loaded
                from (set by ``from_yaml`` / ``from_json``). ``None`` for
                in-memory or test-injected storage. Used in error messages
                to tell the user where to fix things.
        """
        self._storage = storage
        self._cache: Dict[str, CacheEntry] = {}
        self._source_path: Optional[Path] = source_path
        #: In-memory ``alias -> real key`` map. See :meth:`alias`.
        self._aliases: Dict[str, str] = {}

    @property
    def source_path(self) -> Optional[Path]:
        """Absolute path the hub was loaded from, or ``None``."""
        return self._source_path

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
        abs_path = Path(path).resolve()
        storage = YamlConfigStorage(abs_path)
        return cls(storage, source_path=abs_path)

    @classmethod
    def from_json(cls, path: str | Path) -> "ResourceHub":
        """Create hub with JSON file storage.

        Args:
            path: Path to JSON config file

        Returns:
            ResourceHub instance
        """
        from .storage import JsonConfigStorage

        abs_path = Path(path).resolve()
        storage = JsonConfigStorage(abs_path)
        return cls(storage, source_path=abs_path)

    @classmethod
    def auto(cls) -> Optional["ResourceHub"]:
        """Try to install ResourceHub from ``./resources.yaml`` in CWD.

        Behavior:
        - If a hub is already installed, return it unchanged (idempotent).
        - If ``./resources.yaml`` exists, load it, install the singleton,
          and return the new hub.
        - If not found, emit a :class:`ResourceHubWarning` naming the path
          checked and return ``None``. No singleton is installed.

        Never raises. The warning is the early signal that setup is
        incomplete; silent miss would defer the problem to first
        resource resolution.
        """
        if cls._instance is not None:
            return cls._instance

        candidate = (Path.cwd() / "resources.yaml").resolve()
        if not candidate.exists():
            warnings.warn(
                f"No resources.yaml found at {candidate}. "
                "ResourceHub not installed; provider ops will fail at "
                "resolution. Call ResourceHub.from_yaml(<path>) with an "
                "explicit path if your file lives elsewhere.",
                ResourceHubWarning,
                stacklevel=2,
            )
            return None

        hub = cls.from_yaml(candidate)
        cls.set_instance(hub)
        return hub

    @classmethod
    def instance(cls) -> "ResourceHub":
        """Get the global ResourceHub singleton.

        Raises:
            RuntimeError: If no hub has been loaded. The message points at
                ``operonx.bootstrap()`` and ``ResourceHub.from_yaml(...)``
                as the two ways to install one.
        """
        if cls._instance is None:
            raise RuntimeError(
                "ResourceHub not initialized. Install one before resolving resources:\n"
                "  import operonx\n"
                "  operonx.bootstrap()                            # auto-discover ./resources.yaml + .env\n"
                "Or with an explicit path:\n"
                "  from operonx.core.registry import ResourceHub\n"
                "  ResourceHub.set_instance(ResourceHub.from_yaml('path/to/resources.yaml'))"
            )
        return cls._instance

    @classmethod
    def set_instance(cls, hub: "ResourceHub") -> None:
        """Install *hub* as the global singleton (used by ``Operon(...)``)."""
        cls._instance = hub

    @classmethod
    def reset_instance(cls) -> None:
        """Clear the global singleton. For tests only."""
        cls._instance = None

    # ========================================================================
    # Load Config (Lazy)
    # ========================================================================

    def _load_config(self, key: str) -> Optional[YamlModel]:
        """Load a config from storage (lazy, on demand)."""
        key = self._resolve_alias(key)
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

    # ========================================================================
    # Aliases
    # ========================================================================

    def alias(self, alias_key: str, target_key: str) -> None:
        """Point ``alias_key`` at an existing resource, in memory only.

        For naming a *role* at the call site while an operator still
        chooses the resource that fills it::

            hub.alias("llm:scanner", f"llm:{os.environ['LLM_RESOURCE_KEY']}")
            # graph stays literal: LLMOp.of(resource="scanner", ...)

        That keeps the wiring readable to tooling — which can only see a
        literal — without freezing the model choice into the graph.

        **Not** :meth:`register`: that persists through to storage and
        rewrites ``resources.yaml``, losing its comments. This touches
        nothing on disk and lasts for the life of the hub.

        Resolution is one hop, applied on every lookup, so re-aliasing
        later repoints existing call sites. A cached *instance* under the
        old target is unaffected — it is keyed by the real name.

        Args:
            alias_key: The name call sites use, e.g. ``"llm:scanner"``.
            target_key: An existing resource key, e.g. ``"llm:db-gemini-3-flash"``.

        Raises:
            ValueError: The alias would point at itself, or *target_key*
                is itself an alias — one hop only, so a chain that could
                silently become a cycle is refused at declaration time
                rather than hanging at first use.
        """
        if alias_key == target_key:
            raise ValueError(f"alias {alias_key!r} cannot point at itself")
        if target_key in self._aliases:
            raise ValueError(
                f"alias target {target_key!r} is itself an alias "
                f"(-> {self._aliases[target_key]!r}); point {alias_key!r} at the "
                "real key instead — aliases resolve one hop only"
            )
        self._aliases[alias_key] = target_key
        LOGGER.debug("Aliased: %s -> %s", alias_key, target_key)

    def aliases(self) -> Dict[str, str]:
        """A copy of the ``alias -> real key`` map."""
        return dict(self._aliases)

    def unalias(self, alias_key: str) -> bool:
        """Drop an alias. Returns True if one was removed."""
        return self._aliases.pop(alias_key, None) is not None

    def _resolve_alias(self, key: str) -> str:
        """One hop, or the key unchanged."""
        return self._aliases.get(key, key)

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
            KeyError: If key not found or resource failed to initialize.
                The message includes ``source_path`` and available keys
                so the user can tell which fix to apply.
            EnvVarUnsetError: If the resource references a ``${VAR}``
                whose env var is unset. Subclass of ``RuntimeError`` for
                backwards compatibility.
        """
        key = self._resolve_alias(key)

        # Return cached instance if available
        if key in self._cache and self._cache[key].instance is not None:
            instance = self._cache[key].instance
            # Refresh the bearer token if this resource carries a provider
            self._refresh_token(instance)
            return instance

        # Load config from storage. ``EnvVarUnsetError`` from missing
        # ``${VAR}`` interpolation propagates as-is (branch 4) — distinct
        # from the "key not found" path below.
        config = self._load_config(key)
        if not config:
            raise KeyError(self._not_found_message(key))

        # Resolve a token reference if configured
        # (api_key: "keycloak:xxx" or "oauth2:xxx")
        resolved_config = self._resolve_token_ref(config)
        create_config = resolved_config or config

        # Lazy initialize resource
        try:
            instance = REGISTRY.create(create_config)
        except Exception as e:
            LOGGER.warning("Failed to create resource '%s': %s", key, e)
            raise KeyError(f"Resource '{key}' failed to initialize: {e}") from e

        if instance is None:
            raise KeyError(f"Cannot create resource for '{key}': factory returned None")

        # Attach the provider so a later cache hit can refresh the token.
        ref = _split_token_ref(getattr(config, "api_key", None))
        if resolved_config is not None and ref is not None:
            provider = self.get(f"{ref[0]}:{ref[1]}")
            instance._token_provider = provider
            # Back-compat alias: this attribute predates oauth2 and is
            # asserted on by name, so both point at the same object.
            instance._keycloak_provider = provider
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
            raise KeyError(self._not_found_message(key))
        return config

    def _not_found_message(self, key: str) -> str:
        """Build a 'not found' error string with available keys + source.

        Disambiguates branch (3) from branch (1)/(2): the user knows the
        hub is configured (``source_path`` is shown) and that the key
        simply isn't present (``Available`` is shown).
        """
        try:
            available = sorted(self._storage.load_all().keys())
        except Exception:
            available = sorted(self._cache.keys())
        source = str(self._source_path) if self._source_path else "<in-memory storage>"
        if available:
            avail_str = ", ".join(repr(k) for k in available)
            return f"Resource '{key}' not found in {source}.\n  Available: [{avail_str}]"
        return (
            f"Resource '{key}' not found in {source}.\n  (No resources loaded — file may be empty.)"
        )

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

    def _refresh_token(self, instance) -> None:
        """Refresh the bearer token on a cached instance, if it has one.

        Works for every provider in :data:`TOKEN_REF_PREFIXES` — each one
        exposes ``get_token()`` and nothing here knows which scheme minted
        it. Cheap on the hot path: the provider returns its cached token
        until expiry, so this is a dict lookup plus a clock read.
        """
        provider = getattr(instance, "_token_provider", None)
        if provider is None:
            # Pre-oauth2 instances (and anything built by hand) may carry
            # only the old attribute.
            provider = getattr(instance, "_keycloak_provider", None)
        if provider is None:
            return
        fresh_token = provider.get_token()
        if hasattr(instance, "client"):
            instance.client.api_key = fresh_token
        if hasattr(instance, "config"):
            instance.config.api_key = fresh_token

    #: Deprecated alias kept because the name was public in practice.
    _refresh_keycloak = _refresh_token

    def _resolve_token_ref(self, config: YamlModel) -> Optional[YamlModel]:
        """Swap an ``api_key: "<provider>:<name>"`` for a live token.

        Returns a copy of *config* carrying the token, or None when the
        api_key is a literal — which is also what a non-``api_key``
        resource gets, so callers can pass any config in.
        """
        ref = _split_token_ref(getattr(config, "api_key", None))
        if ref is None:
            return None
        category, name = ref

        try:
            resolved_token = self._resolve_api_key(config.api_key)
        except Exception as e:
            raise KeyError(
                f"{category} '{name}' failed ({type(e).__name__}: {e})"
            ) from e

        config_dict = config.model_dump()
        config_dict["api_key"] = resolved_token
        return type(config).model_validate(config_dict)

    #: Deprecated alias — the method was keycloak-only before oauth2.
    _resolve_keycloak = _resolve_token_ref

    def keycloak(self, key: str) -> "KeycloakTokenProvider":
        """Get KeycloakTokenProvider by key.

        Args:
            key: Keycloak config identifier (e.g., 'myapp')

        Returns:
            KeycloakTokenProvider instance with get_token() method
        """
        return self.get(f"keycloak:{key}")

    # ========================================================================
    # API Key Resolution (for keycloak references)
    # ========================================================================

    def _resolve_api_key(self, api_key: str) -> str:
        """Resolve an api_key value, following token-provider references.

        A value of the form ``"<provider>:<name>"`` where *provider* is one
        of :data:`TOKEN_REF_PREFIXES` is replaced by a live bearer token
        from that resource. Anything else is returned untouched.

        Args:
            api_key: Either a static key or a ``'<provider>:<name>'``
                reference.

        Returns:
            Resolved API key string.

        Example:
            # Static key - returned as-is
            _resolve_api_key("sk-xxx") -> "sk-xxx"

            # Keycloak reference - fetches token
            _resolve_api_key("keycloak:myapp") -> "eyJ..." (actual token)

            # OAuth2 client_credentials reference - fetches token
            _resolve_api_key("oauth2:databricks") -> "eyJ..." (actual token)
        """
        ref = _split_token_ref(api_key)
        if ref is None:
            return api_key
        category, name = ref
        provider = self.get(f"{category}:{name}")
        return provider.get_token()

    def oauth2(self, key: str) -> "OAuth2TokenProvider":  # noqa: F821
        """Get an OAuth2TokenProvider by key.

        Args:
            key: OAuth2 config identifier (e.g. ``'databricks'``).

        Returns:
            OAuth2TokenProvider instance with a ``get_token()`` method.
        """
        return self.get(f"oauth2:{key}")

    # ========================================================================
    # Warmup
    # ========================================================================

    async def warmup(self, key: str, **kwargs: Any) -> None:
        """Pre-warm a resource's connection (e.g. LLM prompt cache).

        Forces lazy-load of the resource, then calls its ``warmup()``
        method if it exists. For LLM providers this typically sends a
        minimal API request to establish the TCP+TLS connection and
        optionally seed the server-side prompt cache.

        Args:
            key: Registry key (e.g. ``"llm:default"``)
            **kwargs: Passed to the resource's ``warmup()`` method
                      (e.g. ``system_prompt="..."`` for LLMs)

        Example::

            hub = ResourceHub.instance()
            await hub.warmup("llm:default", system_prompt=sys_prompt)
        """
        try:
            resource = self.get(key)
            if hasattr(resource, "warmup"):
                result = resource.warmup(**kwargs)
                # Support both sync and async warmup methods
                if hasattr(result, "__await__"):
                    await result
                LOGGER.debug("Warmup completed: %s", key)
            else:
                LOGGER.debug("Resource '%s' has no warmup method, skipping", key)
        except Exception as exc:
            LOGGER.warning("Warmup failed for '%s' (non-fatal): %s", key, exc)

    # ========================================================================
    # Health Check
    # ========================================================================

    def health_check(
        self,
        keys: Optional[List[str]] = None,
        *,
        probe: bool = False,
        timeout: float = 2.0,
    ) -> HealthCheckResult:
        """Check all or specified resources: does each build, and answer?

        Args:
            keys: Keys to check. None checks every configured resource.
            probe: Also open a TCP connection to whatever address the
                config points at. Building a client opens no socket, so
                without this a hub full of corporate endpoints reports
                healthy on a laptop with the VPN down — and the real
                symptom arrives later as every call timing out one at a
                time. Resources with no address (in-memory, filesystem)
                are built and not probed.

                Off by default: this method has always meant "does it
                construct", and flipping that would quietly change the
                answer for every existing caller. ``require_reachable``
                turns it on.
            timeout: Seconds to wait for the connection. Short on purpose:
                the point is to fail at startup rather than across a batch,
                so a slow answer is as useful to us as no answer.

        Returns:
            HealthCheckResult. Never raises for an unhealthy resource — see
            ``require_reachable`` for the version that stops the flow.

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
                instance = self.get(key)
            except Exception as e:
                results[key] = False
                errors[key] = str(e)
                LOGGER.warning("Health check failed for '%s': %s", key, e)
                continue

            if not probe:
                results[key] = True
                continue

            reason = self._probe_endpoint(key, instance, timeout)
            results[key] = reason is None
            if reason is not None:
                errors[key] = reason
                LOGGER.warning("Resource '%s' unreachable: %s", key, reason)

        return HealthCheckResult(
            results=results,
            errors=errors,
        )

    def _probe_endpoint(self, key: str, instance: Any, timeout: float) -> Optional[str]:
        """Why *key*'s address did not answer, or None. Local → None."""
        from .shortcuts.reachability import endpoint_of, probe as _probe

        config = getattr(instance, "config", None) or self.get_config(key)
        if config is None:
            return None
        address = endpoint_of(config)
        if address is None:
            return None
        return _probe(address[0], address[1], timeout=timeout)

    def require_reachable(
        self,
        *keys: str,
        timeout: float = 2.0,
    ) -> HealthCheckResult:
        """Check the named resources and **raise** if any cannot be reached.

        No arguments checks every configured resource::

            hub.require_reachable()                       # all of them
            hub.require_reachable("llm:scorer", "embedding:corpus")

        Every failure is collected before raising, so one run names
        everything that is missing rather than the first thing.

        Raises:
            ResourceUnreachable: when any checked resource failed.
        """
        result = self.health_check(list(keys) or None, probe=True, timeout=timeout)
        if not result.healthy:
            raise ResourceUnreachable(
                {k: result.errors.get(k, "unhealthy") for k in result.failed}
            )
        return result
