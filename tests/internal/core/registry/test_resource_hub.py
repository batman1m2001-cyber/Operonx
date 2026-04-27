"""Tests for the ResourceHub and ConfigRegistry system."""

import os
import warnings
from typing import ClassVar

import pytest

from operon.core.registry import (
    REGISTRY,
    BOOTSTRAP_ENV_PATHS,
    CacheEntry,
    ConfigRegistry,
    EnvVarUnsetError,
    HealthCheckResult,
    ResourceHub,
    ResourceHubWarning,
)
from operon.core.utils.yaml_model import YamlModel

# ============================================================================
# Test Fixtures: Mock Config and Service
# ============================================================================


class MockServiceConfig(YamlModel):
    """Mock config for testing."""

    _category: ClassVar[str] = "service"

    name: str = "default"
    host: str = "localhost"
    port: int = 8080


class MockService:
    """Mock service for testing."""

    def __init__(self, config: MockServiceConfig):
        self.config = config
        self.name = config.name
        self.host = config.host
        self.port = config.port

    def __repr__(self):
        return f"MockService({self.name}@{self.host}:{self.port})"


def mock_service_factory(config: MockServiceConfig) -> MockService:
    """Factory function to create MockService from config."""
    return MockService(config)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def cleanup_registries():
    """Give each test a clean REGISTRY, then restore outer state after.

    Saves whatever plugins the outer suite registered (e.g. from
    ``operon.providers``), clears the registry so the test gets a blank
    slate, then restores the saved state on teardown.
    """
    reg = REGISTRY
    saved_entries = dict(reg._entries)
    saved_class_entries = dict(reg._class_entries)
    reg._entries.clear()
    reg._class_entries.clear()
    try:
        yield
    finally:
        reg._entries = saved_entries
        reg._class_entries = saved_class_entries


@pytest.fixture
def registry():
    """Get fresh registry instance."""
    return REGISTRY


@pytest.fixture
def hub(tmp_path, registry):
    """Create a ResourceHub with YAML storage for testing."""
    config_file = tmp_path / "resources.yaml"
    hub = ResourceHub.from_yaml(config_file)

    # Register mock config and factory
    registry.register(MockServiceConfig, mock_service_factory)

    return hub


@pytest.fixture
def mock_config():
    """Create a mock config for testing."""
    return MockServiceConfig(name="test-service", host="example.com", port=9000)


# ============================================================================
# Tests: ConfigRegistry
# ============================================================================


class TestConfigRegistry:
    """Test ConfigRegistry functionality."""

    def test_register_config(self, registry):
        """Test registering a config class with factory."""
        registry.register(MockServiceConfig, mock_service_factory)

        # Should be findable by category
        cls = registry.get_class("service")
        assert cls == MockServiceConfig

        # Should also be findable by class name
        cls = registry.get_class("MockServiceConfig")
        assert cls == MockServiceConfig

    def test_get_nonexistent_class(self, registry):
        """Test getting non-existent class returns None."""
        result = registry.get_class("NonExistentConfig")
        assert result is None

    def test_create_instance(self, registry):
        """Test creating instance from config."""
        registry.register(MockServiceConfig, mock_service_factory)

        config = MockServiceConfig(name="test", host="localhost", port=8080)
        instance = registry.create(config)

        assert isinstance(instance, MockService)
        assert instance.name == "test"
        assert instance.host == "localhost"
        assert instance.port == 8080

    def test_create_no_factory_raises(self, registry):
        """Test create raises error when no factory registered."""

        class UnregisteredConfig(YamlModel):
            value: str = "test"

        config = UnregisteredConfig()
        with pytest.raises(ValueError, match="No factory registered"):
            registry.create(config)

    def test_duplicate_category_raises(self, registry):
        """Test that registering duplicate category raises error."""

        class ConfigA(YamlModel):
            _category: ClassVar[str] = "test"

        class ConfigB(YamlModel):
            _category: ClassVar[str] = "test"

        registry.register(ConfigA, lambda c: c)

        with pytest.raises(ValueError, match="Duplicate category"):
            registry.register(ConfigB, lambda c: c)

    def test_different_categories(self, registry):
        """Test that different categories resolve to different classes."""

        class ConfigA(YamlModel):
            _category: ClassVar[str] = "llm"
            value: str = "a"

        class ConfigB(YamlModel):
            _category: ClassVar[str] = "embedding"
            value: str = "b"

        registry.register(ConfigA, lambda c: c)
        registry.register(ConfigB, lambda c: c)

        # Should resolve to different classes based on category
        assert registry.get_class("llm") == ConfigA
        assert registry.get_class("embedding") == ConfigB

    def test_categories_list(self, registry):
        """Test listing all categories."""
        registry.register(MockServiceConfig, mock_service_factory)

        categories = registry.categories()
        assert "service" in categories

    def test_clear(self, registry):
        """Test clearing all registrations."""
        registry.register(MockServiceConfig, mock_service_factory)
        registry.clear()

        assert registry.get_class("MockServiceConfig") is None


# ============================================================================
# Tests: ResourceHub Creation
# ============================================================================


class TestHubCreation:
    """Test creating ResourceHub instances."""

    def test_from_yaml(self, tmp_path):
        """Test creating hub from YAML file."""
        config_file = tmp_path / "test.yaml"
        hub = ResourceHub.from_yaml(config_file)
        assert hub is not None

    def test_from_json(self, tmp_path):
        """Test creating hub from JSON file."""
        config_file = tmp_path / "test.json"
        hub = ResourceHub.from_json(config_file)
        assert hub is not None


# ============================================================================
# Tests: Resource Registration
# ============================================================================


class TestResourceRegistration:
    """Test registering and retrieving resources."""

    def test_register_and_get(self, hub, mock_config):
        """Test registering and retrieving a resource."""
        key = hub.register(mock_config)

        # Verify key format
        assert "mockservice:" in key.lower() or "test-service" in key

        # Verify resource is retrievable
        assert hub.has(key)
        service = hub.get(key)
        assert isinstance(service, MockService)
        assert service.name == "test-service"
        assert service.host == "example.com"
        assert service.port == 9000

    def test_register_with_custom_key(self, hub, mock_config):
        """Test registering with a custom key."""
        key = hub.register(mock_config, registry_key="custom:my-service")
        assert key == "custom:my-service"
        assert hub.has("custom:my-service")

    def test_get_config(self, hub, mock_config):
        """Test retrieving config object."""
        key = hub.register(mock_config)
        config = hub.get_config(key)

        assert isinstance(config, MockServiceConfig)
        assert config.name == "test-service"


# ============================================================================
# Tests: Resource Removal
# ============================================================================


class TestResourceRemoval:
    """Test removing resources."""

    def test_remove_existing(self, hub, mock_config):
        """Test removing an existing resource."""
        key = hub.register(mock_config)
        assert hub.has(key)

        removed = hub.remove(key)
        assert removed is True
        assert not hub.has(key)

    def test_remove_nonexistent(self, hub):
        """Test removing non-existent key returns False."""
        removed = hub.remove("nonexistent:key")
        assert removed is False

    def test_clear_all(self, hub, registry):
        """Test clearing all resources."""
        hub.register(MockServiceConfig(name="service1"))
        hub.register(MockServiceConfig(name="service2"))
        hub.register(MockServiceConfig(name="service3"))

        assert len(hub.keys()) == 3

        hub.clear()
        assert len(hub.keys()) == 0


# ============================================================================
# Tests: Key Operations
# ============================================================================


class TestKeyOperations:
    """Test key listing and checking operations."""

    def test_keys_empty(self, hub):
        """Test keys on empty hub."""
        assert hub.keys() == []

    def test_keys_with_resources(self, hub):
        """Test listing all keys."""
        hub.register(MockServiceConfig(name="service1"))
        hub.register(MockServiceConfig(name="service2"))

        keys = hub.keys()
        assert len(keys) == 2

    def test_has_existing(self, hub, mock_config):
        """Test has returns True for existing key."""
        key = hub.register(mock_config)
        assert hub.has(key) is True

    def test_has_nonexistent(self, hub):
        """Test has returns False for non-existent key."""
        assert hub.has("nonexistent:key") is False


# ============================================================================
# Tests: Error Handling
# ============================================================================


class TestErrorHandling:
    """Test error handling."""

    def test_get_nonexistent_raises(self, hub):
        """Test get raises KeyError for non-existent key."""
        with pytest.raises(KeyError, match="not found"):
            hub.get("nonexistent:key")

    def test_get_config_nonexistent_raises(self, hub):
        """Test get_config raises KeyError for non-existent key."""
        with pytest.raises(KeyError, match="not found"):
            hub.get_config("nonexistent:key")


# ============================================================================
# Tests: Singleton Pattern
# ============================================================================


class TestSingletonPattern:
    """Test singleton instance management."""

    def test_set_and_get_instance(self, tmp_path):
        """Test setting and getting singleton instance."""
        # Clear any existing instance
        ResourceHub._instance = None

        config_file = tmp_path / "singleton.yaml"
        hub = ResourceHub.from_yaml(config_file)
        ResourceHub.set_instance(hub)

        singleton = ResourceHub.instance()
        assert singleton is hub

        # Clean up
        ResourceHub._instance = None

    def test_instance_not_initialized_raises(self):
        """Test instance raises error if not initialized."""
        ResourceHub._instance = None

        with pytest.raises(RuntimeError, match="not initialized"):
            ResourceHub.instance()


# ============================================================================
# Tests: File Persistence
# ============================================================================


class TestFilePersistence:
    """Test file-based persistence."""

    def test_yaml_persistence(self, tmp_path, registry):
        """Test YAML file persistence."""
        config_file = tmp_path / "persist.yaml"

        # Register config class and handler
        registry.register(MockServiceConfig, mock_service_factory)

        # Create hub and register resource
        hub1 = ResourceHub.from_yaml(config_file)
        config = MockServiceConfig(name="persistent", host="example.com", port=9000)
        key = hub1.register(config)
        hub1.close()

        # Create new hub from same file
        hub2 = ResourceHub.from_yaml(config_file)

        # Verify resource is loaded
        assert hub2.has(key)
        service = hub2.get(key)
        assert service.name == "persistent"
        assert service.host == "example.com"
        assert service.port == 9000
        hub2.close()

    def test_json_persistence(self, tmp_path, registry):
        """Test JSON file persistence."""
        config_file = tmp_path / "persist.json"

        # Register config class and handler
        registry.register(MockServiceConfig, mock_service_factory)

        # Create hub and register resource
        hub1 = ResourceHub.from_json(config_file)
        config = MockServiceConfig(name="json-service", host="json.example.com", port=8888)
        key = hub1.register(config)
        hub1.close()

        # Create new hub from same file
        hub2 = ResourceHub.from_json(config_file)

        # Verify resource is loaded
        assert hub2.has(key)
        service = hub2.get(key)
        assert service.name == "json-service"
        hub2.close()


# ============================================================================
# Tests: Type-based Registration
# ============================================================================


class MockLLMConfig(YamlModel):
    """Mock LLM config for testing category-based registration."""

    _category: ClassVar[str] = "llm"

    name: str = "default"
    model: str = "gpt-4"


class MockEmbeddingConfig(YamlModel):
    """Mock embedding config for testing."""

    _category: ClassVar[str] = "embedding"

    name: str = "default"
    dimensions: int = 1024


class MockLLMService:
    """Mock LLM service."""

    def __init__(self, config: MockLLMConfig):
        self.config = config
        self.model = config.model


class MockEmbeddingService:
    """Mock embedding service."""

    def __init__(self, config: MockEmbeddingConfig):
        self.config = config
        self.dimensions = config.dimensions


def mock_llm_factory(config: MockLLMConfig) -> MockLLMService:
    return MockLLMService(config)


def mock_embedding_factory(config: MockEmbeddingConfig) -> MockEmbeddingService:
    return MockEmbeddingService(config)


class TestCategoryBasedRegistration:
    """Test category-based config registration."""

    def test_register_with_category(self, registry):
        """Test registering config class with category."""
        registry.register(MockLLMConfig, mock_llm_factory)

        assert "llm" in registry.categories()
        assert registry.get_class("llm") == MockLLMConfig

    def test_get_config_class_by_category(self, registry):
        """Test looking up config class by category."""
        registry.register(MockLLMConfig, mock_llm_factory)

        # Lookup by category
        result = registry.get_class("llm")
        assert result == MockLLMConfig

        # Lookup by class name should also work
        result = registry.get_class("MockLLMConfig")
        assert result == MockLLMConfig

    def test_load_config_by_category(self, tmp_path, registry):
        """Test loading config from YAML using category from key prefix."""
        registry.register(MockLLMConfig, mock_llm_factory)

        config_file = tmp_path / "resources.yaml"
        config_file.write_text("""
llm:test-model:
  name: test
  model: gpt-4-turbo
""")

        hub = ResourceHub.from_yaml(config_file)

        assert hub.has("llm:test-model")
        service = hub.get("llm:test-model")
        assert isinstance(service, MockLLMService)
        assert service.model == "gpt-4-turbo"
        hub.close()

    def test_load_config_backward_compatible_class(self, tmp_path, registry):
        """Test loading config using old '_class' field (backward compatible)."""
        registry.register(MockLLMConfig, mock_llm_factory)

        config_file = tmp_path / "resources.yaml"
        config_file.write_text("""
llm:legacy-model:
  _class: MockLLMConfig
  name: legacy
  model: gpt-3.5-turbo
""")

        hub = ResourceHub.from_yaml(config_file)

        assert hub.has("llm:legacy-model")
        service = hub.get("llm:legacy-model")
        assert isinstance(service, MockLLMService)
        assert service.model == "gpt-3.5-turbo"
        hub.close()

    def test_category_extracted_from_key_prefix(self, tmp_path, registry):
        """Test that category is correctly extracted from key prefix."""
        registry.register(MockLLMConfig, mock_llm_factory)
        registry.register(MockEmbeddingConfig, mock_embedding_factory)

        config_file = tmp_path / "resources.yaml"
        config_file.write_text("""
llm:my-llm:
  name: llm-test
  model: gpt-4

embedding:my-embedding:
  name: embed-test
  dimensions: 768
""")

        hub = ResourceHub.from_yaml(config_file)

        # LLM should use MockLLMConfig
        llm_service = hub.get("llm:my-llm")
        assert isinstance(llm_service, MockLLMService)
        assert llm_service.model == "gpt-4"

        # Embedding should use MockEmbeddingConfig
        embed_service = hub.get("embedding:my-embedding")
        assert isinstance(embed_service, MockEmbeddingService)
        assert embed_service.dimensions == 768

        hub.close()


# ============================================================================
# Tests: Health Check
# ============================================================================


class TestHealthCheck:
    """Test health check functionality."""

    def test_health_check_all_healthy(self, hub, mock_config):
        """Test health check when all resources are healthy."""
        hub.register(mock_config, registry_key="service:test1")
        hub.register(MockServiceConfig(name="test2"), registry_key="service:test2")

        result = hub.health_check()

        assert isinstance(result, HealthCheckResult)
        assert result.healthy is True
        assert len(result.passed) == 2
        assert len(result.failed) == 0

    def test_health_check_result_repr(self, hub, mock_config):
        """Test HealthCheckResult string representation."""
        hub.register(mock_config, registry_key="service:test")

        result = hub.health_check()

        assert "HEALTHY" in repr(result)
        assert "1/1" in repr(result)


# ============================================================================
# Tests: CacheEntry
# ============================================================================


class TestCacheEntry:
    """Test CacheEntry dataclass."""

    def test_cache_entry_creation(self, mock_config):
        """Test creating CacheEntry."""
        entry = CacheEntry(config=mock_config)

        assert entry.config == mock_config
        assert entry.instance is None

    def test_cache_entry_with_instance(self, mock_config):
        """Test CacheEntry with instance."""
        service = MockService(mock_config)
        entry = CacheEntry(config=mock_config, instance=service)

        assert entry.config == mock_config
        assert entry.instance == service


# ============================================================================
# Tests: Graceful Error Handling on Resource Init Failure
# ============================================================================


class FailingConfig(YamlModel):
    """Config whose factory always raises (simulates network error)."""

    _category: ClassVar[str] = "failing"

    name: str = "bad"


def failing_factory(config: FailingConfig):
    raise OSError("[Errno -2] Name or service not known")


class MockLLMWithKeyConfig(YamlModel):
    """Mock LLM config with api_key field for keycloak tests."""

    _category: ClassVar[str] = "llm"

    model: str = "gpt-4"
    api_key: str = "sk-test"


class TestGracefulErrorHandling:
    """Test that resource init failures raise KeyError, not raw exceptions."""

    def _fresh_registry(self):
        """Clear REGISTRY entries and return it, avoiding stale category conflicts."""
        REGISTRY.clear()
        return REGISTRY

    def test_get_raises_keyerror_on_factory_failure(self, tmp_path):
        """get() should wrap factory exceptions in KeyError."""
        registry = self._fresh_registry()
        registry.register(FailingConfig, failing_factory)

        config_file = tmp_path / "resources.yaml"
        config_file.write_text("failing:bad:\n  name: bad\n")
        hub = ResourceHub.from_yaml(config_file)

        with pytest.raises(KeyError, match="failed to initialize"):
            hub.get("failing:bad")

        hub.close()

    def test_other_resources_unaffected_by_failing_one(self, tmp_path):
        """A failing resource should not prevent other resources from loading."""
        registry = self._fresh_registry()
        registry.register(FailingConfig, failing_factory)
        registry.register(MockServiceConfig, mock_service_factory)

        config_file = tmp_path / "resources.yaml"
        config_file.write_text(
            "failing:bad:\n"
            "  name: bad\n"
            "\n"
            "service:good:\n"
            "  name: good-service\n"
            "  host: localhost\n"
            "  port: 8080\n"
        )
        hub = ResourceHub.from_yaml(config_file)

        # Failing resource raises KeyError
        with pytest.raises(KeyError, match="failed to initialize"):
            hub.get("failing:bad")

        # Good resource still works
        service = hub.get("service:good")
        assert isinstance(service, MockService)
        assert service.name == "good-service"

        hub.close()

    def test_llm_keycloak_network_failure_raises_keyerror(self, tmp_path):
        """llm() should raise KeyError when keycloak token resolution fails."""
        registry = self._fresh_registry()
        registry.register(MockLLMWithKeyConfig, lambda c: c)

        config_file = tmp_path / "resources.yaml"
        config_file.write_text("llm:my-model:\n  model: gpt-4\n  api_key: keycloak:nonexistent\n")
        hub = ResourceHub.from_yaml(config_file)

        with pytest.raises(KeyError, match="keycloak.*failed"):
            hub.get("llm:my-model")

        hub.close()

    def test_llm_keycloak_failure_preserves_other_llms(self, tmp_path):
        """A keycloak LLM failure should not affect static-key LLMs."""
        registry = self._fresh_registry()
        registry.register(MockLLMWithKeyConfig, lambda c: c)

        config_file = tmp_path / "resources.yaml"
        config_file.write_text(
            "llm:keycloak-model:\n"
            "  model: gpt-4\n"
            "  api_key: keycloak:nonexistent\n"
            "\n"
            "llm:static-model:\n"
            "  model: gpt-4o\n"
            "  api_key: sk-static-key\n"
        )
        hub = ResourceHub.from_yaml(config_file)

        # Keycloak model fails
        with pytest.raises(KeyError, match="keycloak.*failed"):
            hub.get("llm:keycloak-model")

        # Static model still works
        result = hub.get("llm:static-model")
        assert result.api_key == "sk-static-key"

        hub.close()


# ============================================================================
# Tests: ResourceHub.auto() — discovery + idempotent install
# ============================================================================


class TestResourceHubAuto:
    """Test the ``auto()`` discovery method (CWD-only, never raises)."""

    def test_auto_finds_yaml_in_cwd(self, tmp_path, monkeypatch):
        """auto() finds resources.yaml in CWD, installs hub, sets source_path."""
        ResourceHub._instance = None
        cfg = tmp_path / "resources.yaml"
        cfg.write_text("service:default:\n  host: localhost\n  port: 8080\n")
        monkeypatch.chdir(tmp_path)

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning fails the test
            hub = ResourceHub.auto()

        assert hub is not None
        assert ResourceHub.instance() is hub
        assert hub.source_path == cfg.resolve()
        ResourceHub._instance = None

    def test_auto_warns_when_missing(self, tmp_path, monkeypatch):
        """auto() emits ResourceHubWarning and returns None when no file at CWD."""
        ResourceHub._instance = None
        monkeypatch.chdir(tmp_path)

        with pytest.warns(ResourceHubWarning, match="No resources.yaml found"):
            hub = ResourceHub.auto()

        assert hub is None
        assert ResourceHub._instance is None

    def test_auto_idempotent_when_hub_already_set(self, tmp_path, monkeypatch):
        """auto() returns the existing hub unchanged when one is already installed."""
        ResourceHub._instance = None
        cfg = tmp_path / "resources.yaml"
        cfg.write_text("service:a:\n  host: a\n")
        pre_hub = ResourceHub.from_yaml(cfg)
        ResourceHub.set_instance(pre_hub)

        # CWD has no resources.yaml — but auto() should not warn or replace
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        monkeypatch.chdir(empty_dir)

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # would fail if W1 fires
            result = ResourceHub.auto()

        assert result is pre_hub
        assert ResourceHub.instance() is pre_hub
        ResourceHub._instance = None


# ============================================================================
# Tests: warning W2 — unset ${VAR} references at YAML load
# ============================================================================


class TestUnsetEnvVarWarning:
    """Storage scans for ${VAR} references whose env vars are unset."""

    def test_warns_on_unset_env_vars(self, tmp_path, monkeypatch):
        """from_yaml emits W2 listing unset vars and the resources using them."""
        cfg = tmp_path / "resources.yaml"
        cfg.write_text(
            "service:a:\n"
            "  host: ${UNSET_HOST_FOR_TEST}\n"
            "service:b:\n"
            "  host: ${ALSO_UNSET_FOR_TEST}\n"
        )
        monkeypatch.delenv("UNSET_HOST_FOR_TEST", raising=False)
        monkeypatch.delenv("ALSO_UNSET_FOR_TEST", raising=False)

        with pytest.warns(ResourceHubWarning) as records:
            ResourceHub.from_yaml(cfg)

        joined = "\n".join(str(r.message) for r in records)
        assert "UNSET_HOST_FOR_TEST" in joined
        assert "ALSO_UNSET_FOR_TEST" in joined
        assert "service:a" in joined
        assert "service:b" in joined

    def test_no_warning_when_env_vars_set(self, tmp_path, monkeypatch):
        """from_yaml does not warn when every ${VAR} is set."""
        cfg = tmp_path / "resources.yaml"
        cfg.write_text("service:a:\n  host: ${SET_HOST_FOR_TEST}\n")
        monkeypatch.setenv("SET_HOST_FOR_TEST", "actual-value")

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning fails
            ResourceHub.from_yaml(cfg)

    def test_no_warning_when_default_provided(self, tmp_path):
        """${VAR:default} syntax never warns even when env var is unset."""
        cfg = tmp_path / "resources.yaml"
        cfg.write_text("service:a:\n  host: ${MAYBE_UNSET_FOR_TEST:fallback}\n")

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            ResourceHub.from_yaml(cfg)


# ============================================================================
# Tests: error branches — disambiguated messages on get()
# ============================================================================


class TestDisambiguatedErrors:
    """The get() error message tells the user *which* fix to apply."""

    def test_branch3_includes_source_path_and_available_keys(
        self, tmp_path, registry
    ):
        """Key-not-found error names the source file and lists available keys."""
        cfg = tmp_path / "resources.yaml"
        cfg.write_text(
            "service:alpha:\n  host: a\n"
            "service:beta:\n  host: b\n"
        )
        registry.register(MockServiceConfig, mock_service_factory)
        hub = ResourceHub.from_yaml(cfg)

        with pytest.raises(KeyError) as exc:
            hub.get("service:gamma")

        msg = str(exc.value)
        assert str(cfg.resolve()) in msg
        assert "service:alpha" in msg
        assert "service:beta" in msg

    def test_branch4_env_var_unset_uses_envvarunseterror(
        self, tmp_path, monkeypatch
    ):
        """Unresolved ${VAR} at get() time raises EnvVarUnsetError (RuntimeError)."""
        cfg = tmp_path / "resources.yaml"
        cfg.write_text("service:a:\n  host: ${UNSET_AT_GET_FOR_TEST}\n")
        monkeypatch.delenv("UNSET_AT_GET_FOR_TEST", raising=False)

        # Suppress W2 here — we're testing the error path, not the warning.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceHubWarning)
            hub = ResourceHub.from_yaml(cfg)

        with pytest.raises(EnvVarUnsetError) as exc:
            hub.get("service:a")

        assert "UNSET_AT_GET_FOR_TEST" in str(exc.value)
        # Backwards-compat: must still be catchable as RuntimeError
        assert isinstance(exc.value, RuntimeError)


# ============================================================================
# Tests: operon.bootstrap()
# ============================================================================


class TestBootstrap:
    """Test the top-level operon.bootstrap() convenience."""

    def test_bootstrap_with_explicit_path(self, tmp_path, monkeypatch):
        """bootstrap(resources=...) installs the hub from the given file."""
        import operon

        ResourceHub._instance = None
        cfg = tmp_path / "config.yaml"
        cfg.write_text("service:a:\n  host: a\n")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceHubWarning)
            hub = operon.bootstrap(resources=str(cfg), env=False)

        assert hub is not None
        assert hub.source_path == cfg.resolve()
        assert ResourceHub.instance() is hub
        ResourceHub._instance = None

    def test_bootstrap_idempotent(self, tmp_path, monkeypatch):
        """bootstrap() returns the existing hub unchanged."""
        import operon

        ResourceHub._instance = None
        cfg = tmp_path / "first.yaml"
        cfg.write_text("service:a:\n  host: a\n")
        pre_hub = ResourceHub.from_yaml(cfg)
        ResourceHub.set_instance(pre_hub)

        result = operon.bootstrap(env=False)
        assert result is pre_hub
        ResourceHub._instance = None

    def test_bootstrap_records_env_path(self, tmp_path, monkeypatch):
        """bootstrap(env=True) appends the .env path to BOOTSTRAP_ENV_PATHS."""
        import operon

        ResourceHub._instance = None
        BOOTSTRAP_ENV_PATHS.clear()
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n")
        monkeypatch.chdir(tmp_path)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceHubWarning)
            operon.bootstrap(env=True)

        assert (tmp_path / ".env").resolve() in BOOTSTRAP_ENV_PATHS
        assert os.environ.get("FOO") == "bar"
        BOOTSTRAP_ENV_PATHS.clear()
        ResourceHub._instance = None
