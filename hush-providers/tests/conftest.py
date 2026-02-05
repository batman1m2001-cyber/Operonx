"""Pytest configuration and shared fixtures for hush-providers tests."""

import os
import sys
import pytest
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from package root
load_dotenv(Path(__file__).parent.parent / ".env")

# Also try loading from monorepo root
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Get config path from environment
CONFIGS_PATH = Path(os.environ.get("HUSH_CONFIG", ""))

# =============================================================================
# Configuration Validation
# =============================================================================

SETUP_TUTORIAL = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                    HUSH TEST CONFIGURATION REQUIRED                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  To run hush-providers tests, you need to configure API credentials.         ║
║                                                                              ║
║  STEP 1: Create .env file                                                    ║
║  ─────────────────────────────────────────────────────────────────────────── ║
║  Copy .env.template to .env in the project root:                             ║
║                                                                              ║
║    cp .env.template .env                                                     ║
║                                                                              ║
║  STEP 2: Set HUSH_CONFIG path                                                ║
║  ─────────────────────────────────────────────────────────────────────────── ║
║  Add to your .env file:                                                      ║
║                                                                              ║
║    HUSH_CONFIG=/path/to/your/resources.yaml                                  ║
║                                                                              ║
║  STEP 3: Add API keys to .env                                                ║
║  ─────────────────────────────────────────────────────────────────────────── ║
║  At minimum, you need ONE LLM provider configured:                           ║
║                                                                              ║
║  Option A - OpenRouter (recommended, supports many models):                  ║
║    OPENROUTER_API_KEY=sk-or-v1-your-key                                      ║
║    Get key at: https://openrouter.ai/keys                                    ║
║                                                                              ║
║  Option B - OpenAI:                                                          ║
║    OPENAI_API_KEY=sk-proj-your-key                                           ║
║    Get key at: https://platform.openai.com/api-keys                          ║
║                                                                              ║
║  STEP 4: Configure resources.yaml                                            ║
║  ─────────────────────────────────────────────────────────────────────────── ║
║  Ensure your resources.yaml has at least one LLM config:                     ║
║                                                                              ║
║    llm:gpt-4o:                                                               ║
║      type: openai                                                            ║
║      api_key: ${OPENAI_API_KEY}                                              ║
║      base_url: https://api.openai.com/v1                                     ║
║      model: gpt-4o                                                           ║
║                                                                              ║
║  For embedding tests, add:                                                   ║
║    embedding:openai:                                                         ║
║      type: embedding                                                         ║
║      api_type: openai                                                        ║
║      api_key: ${OPENAI_API_KEY}                                              ║
║      base_url: https://api.openai.com/v1                                     ║
║      model: text-embedding-3-small                                           ║
║      dimensions: 1536                                                        ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


def pytest_configure(config):
    """Validate test configuration before running tests."""
    # Check if HUSH_CONFIG is set
    if not os.environ.get("HUSH_CONFIG"):
        print(SETUP_TUTORIAL, file=sys.stderr)
        pytest.exit(
            "HUSH_CONFIG environment variable not set. "
            "Please follow the setup tutorial above.",
            returncode=1
        )

    # Check if config file exists
    if not CONFIGS_PATH.exists():
        print(SETUP_TUTORIAL, file=sys.stderr)
        pytest.exit(
            f"Config file not found: {CONFIGS_PATH}\n"
            "Please create resources.yaml or update HUSH_CONFIG path.",
            returncode=1
        )

    # Register custom markers
    config.addinivalue_line(
        "markers",
        "integration: mark test as integration test (requires real API credentials)"
    )


# =============================================================================
# Session Fixtures
# =============================================================================

@pytest.fixture(scope="session", autouse=True)
def setup_resource_hub():
    """Setup ResourceHub with test configurations for the entire test session."""
    from hush.core.registry import ResourceHub, set_global_hub

    # Import plugins to auto-register config classes and factory handlers
    from hush.providers.registry import LLMPlugin, EmbeddingPlugin, RerankPlugin

    # Create hub from config file
    hub = ResourceHub.from_yaml(CONFIGS_PATH)

    # Set as global and singleton
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    yield hub

    # Cleanup
    ResourceHub._instance = None


@pytest.fixture
def hub(setup_resource_hub):
    """Get the ResourceHub instance."""
    return setup_resource_hub
