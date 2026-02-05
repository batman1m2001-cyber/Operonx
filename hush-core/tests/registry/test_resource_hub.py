"""Tests for the ResourceHub and ConfigRegistry system."""

import pytest
from typing import Any, ClassVar

from hush.core.utils.yaml_model import YamlModel
from hush.core.registry import (
    ResourceHub,
    ConfigRegistry,
    REGISTRY,
    CacheEntry,
    HealthCheckResult,
)


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
    """Clean up registries before and after each test."""
    # Reset registry singleton
    ConfigRegistry.reset()

    yield

    # Reset after test
    ConfigRegistry.reset()


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
    return MockServiceConfig(
        name="test-service",
        host="example.com",
        port=9000
    )


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
