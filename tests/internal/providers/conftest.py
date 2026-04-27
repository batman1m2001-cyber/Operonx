"""Pytest configuration and shared fixtures for operonx-providers tests.

Discovery: walks up from this file to find the project root (nearest
``pyproject.toml``), then loads ``.env`` and ``resources.yaml`` from there.
If ``resources.yaml`` is missing, all provider tests skip gracefully.
"""

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv


def _find_project_root(start: Path) -> Path:
    """Walk up from *start* to find nearest directory containing pyproject.toml."""
    p = start.resolve()
    while p != p.parent:
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    raise RuntimeError(f"No pyproject.toml found walking up from {start}")


ROOT = _find_project_root(Path(__file__).parent)
ENV_FILE = ROOT / ".env"
RESOURCES_FILE = ROOT / "resources.yaml"

# Auto-load .env from project root (non-override, preserves shell env)
if ENV_FILE.exists():
    load_dotenv(ENV_FILE, override=False)

# CI-safe defaults for env vars that ``resources.yaml`` references. Many
# provider tests instantiate ops (``LLMOp.of(resource="gpt-4o")``) which
# resolves the config via ``hub.get(...)`` — that triggers ``${VAR}``
# interpolation and would raise ``EnvVarUnsetError`` if any referenced
# variable is missing. The actual network calls are mocked by individual
# tests; integration tests that need real credentials are auto-marked
# (see ``pytest_collection_modifyitems`` below) and excluded under the
# ``-m "not integration"`` selector.
#
# ``setdefault`` ensures real values from ``.env`` (loaded above) win on
# developer machines.
_CI_DUMMY_ENV = {
    "OPENAI_API_KEY": "ci-dummy-openai-key",
    "ANTHROPIC_API_KEY": "ci-dummy-anthropic-key",
    "ANTHROPIC_BASE_URL": "https://example.invalid",
    "ANTHROPIC_MODEL": "claude-haiku",
    "DEEPINFRA_API_KEY": "ci-dummy-deepinfra-key",
    "BGE_M3_EMBEDDING_PATH": "/tmp/ci-dummy-embedding",
    "PINECONE_API_KEY": "ci-dummy-pinecone-key",
    "BGE_M3_RERANKER_PATH": "/tmp/ci-dummy-reranker",
    "LANGFUSE_PUBLIC_KEY": "ci-dummy-langfuse-public",
    "LANGFUSE_SECRET_KEY": "ci-dummy-langfuse-secret",
    "LANGFUSE_HOST": "https://example.invalid",
}
for _key, _val in _CI_DUMMY_ENV.items():
    os.environ.setdefault(_key, _val)

# =============================================================================
# Skip-all hook — if resources.yaml is missing, skip every test in this suite
# =============================================================================


def pytest_configure(config):
    """Register markers."""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test (requires real API credentials)"
    )


def pytest_collection_modifyitems(config, items):
    """Adjust the provider test collection.

    1. If ``resources.yaml`` is missing entirely (e.g. fresh clone with no
       config), skip the whole provider suite — nothing reachable.
    2. Otherwise, auto-mark any test whose class name ends with
       ``Integration`` (or whose function name contains ``_integration``)
       with ``pytest.mark.integration`` so the standard ``-m "not
       integration"`` selector excludes them by default. CI runs without
       real LLM credentials and shouldn't try to dial out.
    """
    if not RESOURCES_FILE.exists():
        print(
            f"resources.yaml not found at {RESOURCES_FILE} — skipping provider tests.",
            file=sys.stderr,
        )
        skip_marker = pytest.mark.skip(reason=f"resources.yaml not found at {RESOURCES_FILE}")
        for item in items:
            item.add_marker(skip_marker)
        return

    integration_marker = pytest.mark.integration
    for item in items:
        cls = getattr(item, "cls", None)
        cls_name = cls.__name__ if cls is not None else ""
        if cls_name.endswith("Integration") or "_integration" in item.name.lower():
            item.add_marker(integration_marker)


# =============================================================================
# Session fixtures
# =============================================================================


@pytest.fixture(scope="session", autouse=True)
def setup_resource_hub():
    """Load ResourceHub from resources.yaml for the whole test session."""
    from operonx.core.registry import ResourceHub

    hub = ResourceHub.from_yaml(RESOURCES_FILE)
    ResourceHub.set_instance(hub)

    yield hub

    ResourceHub.reset_instance()


@pytest.fixture
def hub(setup_resource_hub):
    """Get the ResourceHub instance."""
    return setup_resource_hub
