"""Example demonstrating the hush-core registry system.

This example shows:
1. Creating a ResourceHub from a YAML file
2. Registering plugins from different packages
3. Accessing resources
4. Dynamic registration
5. Singleton pattern
"""

from pathlib import Path

# ============================================================================
# Example 1: Basic Usage
# ============================================================================


def example_basic():
    """Basic usage: Load from YAML and register plugins."""
    from hush.core.registry import ResourceHub

    # Create hub from YAML config
    config_path = Path(__file__).parent.parent.parent / "configs" / "resources.yaml"
    hub = ResourceHub.from_yaml(config_path)

    # Check what's in the config file (before plugins)
    print("📦 Config file loaded")
    print(f"   Keys found: {hub.keys()}")
    print()

    # Register plugins from hush-providers
    try:
        from hush.providers.registry import EmbeddingPlugin, LLMPlugin, RerankPlugin

        print("🔌 Registering plugins...")
        hub.register_plugins(LLMPlugin, EmbeddingPlugin, RerankPlugin)
        print("   ✓ LLMPlugin")
        print("   ✓ EmbeddingPlugin")
        print("   ✓ RerankPlugin")
        print()

        # Now resources are instantiated and available
        print("🎯 Available resources:")
        for key in sorted(hub.keys()):
            print(f"   - {key}")
        print()

        # Access resources
        print("📥 Accessing resources...")
        if hub.has("llm:gpt-4o"):
            llm = hub.llm("gpt-4o")
            print(f"   ✓ LLM: {llm}")

        if hub.has("embedding:bge-m3"):
            embedding = hub.embedding("bge-m3")
            print(f"   ✓ Embedding: {embedding}")

        if hub.has("reranking:bge-m3"):
            reranker = hub.reranker("bge-m3")
            print(f"   ✓ Reranker: {reranker}")

    except ImportError as e:
        print(f"⚠️  Could not import plugins: {e}")
        print("   Make sure hush-providers is installed")


# ============================================================================
# Example 2: Dynamic Registration
# ============================================================================


def example_dynamic_registration():
    """Demonstrate dynamically adding resources at runtime."""
    from hush.core.registry import ResourceHub

    # Create in-memory hub for this demo
    hub = ResourceHub.from_memory()

    try:
        from hush.providers.llms.config import OpenAIConfig
        from hush.providers.registry import LLMPlugin

        # Register plugin
        hub.register_plugin(LLMPlugin)

        # Add a resource dynamically
        print("➕ Adding new LLM resource...")
        new_config = OpenAIConfig(
            model="gpt-4-turbo",
            api_type="openai",
            api_key="sk-test-key",
            base_url="https://api.openai.com/v1",
        )

        # Register (won't persist since using in-memory storage)
        key = hub.register(new_config, persist=True)
        print(f"   ✓ Registered: {key}")

        # Access it
        llm = hub.llm("gpt-4-turbo")
        print(f"   ✓ Retrieved: {llm}")

    except ImportError:
        print("⚠️  hush-providers not available")


# ============================================================================
# Example 3: Singleton Pattern
# ============================================================================


def example_singleton():
    """Demonstrate singleton pattern for global registry."""
    from hush.core.registry import ResourceHub

    # Initialize singleton
    print("🌍 Setting up global singleton...")
    hub = ResourceHub.from_memory()

    try:
        from hush.providers.registry import LLMPlugin

        hub.register_plugin(LLMPlugin)
    except ImportError:
        pass

    # Set as singleton
    ResourceHub.set_instance(hub)
    print("   ✓ Singleton configured")
    print()

    # Access from anywhere in your code
    print("📡 Accessing singleton from anywhere...")
    global_hub = ResourceHub.instance()
    print(f"   ✓ Got singleton: {global_hub}")
    print(f"   ✓ Same instance: {global_hub is hub}")


# ============================================================================
# Example 4: Custom Plugin
# ============================================================================


def example_custom_plugin():
    """Create and use a custom plugin."""
    from typing import Any, Type

    from hush.core.registry import ResourceConfig, ResourceHub, ResourcePlugin

    # Define a custom config
    class CacheConfig(ResourceConfig):
        """Configuration for a simple cache."""

        host: str = "localhost"
        port: int = 6379
        name: str = "default"

    # Define a simple cache implementation
    class SimpleCache:
        def __init__(self, config: CacheConfig):
            self.config = config
            self._data = {}

        def get(self, key: str):
            return self._data.get(key)

        def set(self, key: str, value: Any):
            self._data[key] = value

        def __repr__(self):
            return f"SimpleCache({self.config.name}@{self.config.host}:{self.config.port})"

    # Define the plugin
    class CachePlugin(ResourcePlugin):
        @classmethod
        def config_class(cls) -> Type[ResourceConfig]:
            return CacheConfig

        @classmethod
        def create(cls, config: ResourceConfig) -> Any:
            return SimpleCache(config)

        @classmethod
        def resource_type(cls) -> str:
            return "cache"

    # Use it!
    print("🔧 Creating custom plugin...")
    hub = ResourceHub.from_memory()
    hub.register_plugin(CachePlugin)
    print("   ✓ CachePlugin registered")
    print()

    # Add a cache resource
    cache_config = CacheConfig(host="localhost", port=6379, name="session-cache")
    key = hub.register(cache_config)
    print(f"   ✓ Registered: {key}")

    # Use the cache
    cache = hub.get(key)
    print(f"   ✓ Cache instance: {cache}")

    cache.set("user:123", {"name": "John Doe"})
    user = cache.get("user:123")
    print(f"   ✓ Cache working: {user}")


# ============================================================================
# Example 5: Testing Pattern
# ============================================================================


def example_testing_pattern():
    """Show how to use in-memory storage for testing."""
    from hush.core.registry import ResourceHub

    print("🧪 Testing pattern with in-memory storage...")

    # Create test hub
    test_hub = ResourceHub.from_memory()
    print("   ✓ In-memory hub created")

    try:
        from hush.providers.llms.config import OpenAIConfig
        from hush.providers.registry import LLMPlugin

        test_hub.register_plugin(LLMPlugin)

        # Add test resources
        test_hub.register(
            OpenAIConfig(
                model="gpt-4",
                api_type="openai",
                api_key="test-key-123",
                base_url="https://api.openai.com/v1",
            )
        )

        print(f"   ✓ Test resources: {test_hub.keys()}")

        # Clean up is easy
        test_hub.clear(persist=False)
        print(f"   ✓ Cleared: {test_hub.keys()}")

    except ImportError:
        print("   ⚠️  hush-providers not available")


# ============================================================================
# Main
# ============================================================================


def main():
    """Run all examples."""
    examples = [
        ("Basic Usage", example_basic),
        ("Dynamic Registration", example_dynamic_registration),
        ("Singleton Pattern", example_singleton),
        ("Custom Plugin", example_custom_plugin),
        ("Testing Pattern", example_testing_pattern),
    ]

    for i, (name, example_fn) in enumerate(examples, 1):
        print("=" * 70)
        print(f"Example {i}: {name}")
        print("=" * 70)
        try:
            example_fn()
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback

            traceback.print_exc()
        print()


if __name__ == "__main__":
    main()
