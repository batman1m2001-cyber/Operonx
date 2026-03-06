"""Pytest configuration and shared fixtures for hush-providers tests."""

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env file from package root (hush-providers/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Also try loading from monorepo root (Hush-ai/)
# __file__ = hush-providers/tests/conftest.py → .parent.parent = hush-providers/ → .parent = Hush-ai/
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

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
    # Check if HUSH_CONFIG is set - skip tests if not configured (for CI)
    if not os.environ.get("HUSH_CONFIG"):
        print(
            "HUSH_CONFIG not set - skipping provider tests. "
            "Set HUSH_CONFIG to run these tests locally.",
            file=sys.stderr,
        )
        return

    # Check if config file exists
    if not CONFIGS_PATH.exists():
        print(
            f"Config file not found: {CONFIGS_PATH} - skipping provider tests.",
            file=sys.stderr,
        )
        return

    # Register custom markers
    config.addinivalue_line(
        "markers", "integration: mark test as integration test (requires real API credentials)"
    )


def pytest_collection_modifyitems(config, items):
    """Skip all tests if HUSH_CONFIG is not set."""
    if not os.environ.get("HUSH_CONFIG") or not CONFIGS_PATH.exists():
        skip_marker = pytest.mark.skip(reason="HUSH_CONFIG not set or config file not found")
        for item in items:
            item.add_marker(skip_marker)


# =============================================================================
# Session Fixtures
# =============================================================================


@pytest.fixture(scope="session", autouse=True)
def setup_resource_hub():
    """Setup ResourceHub with test configurations for the entire test session."""
    from hush.core.registry import ResourceHub, set_global_hub

    # Import plugins to auto-register config classes and factory handlers

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
