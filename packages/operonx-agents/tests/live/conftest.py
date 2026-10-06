"""Live tests: real gateways, tiny spend. Credentials come from the
callbot's ``.env`` (read-only, never printed); without it every test here
skips. Run with ``-m live``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

CALLBOT_ENV = Path(os.environ.get("CALLBOT_ENV", "/home/thanglq/callbot-wt/refactor/.env"))
RESOURCES = Path(__file__).with_name("resources.yaml")


def load_env() -> bool:
    if not CALLBOT_ENV.exists():
        return False
    from dotenv import load_dotenv

    load_dotenv(CALLBOT_ENV, override=False)
    return bool(os.environ.get("LLM_API_KEY") and os.environ.get("QWEN_API_KEY"))


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/live/" in str(item.fspath):
            item.add_marker(pytest.mark.live)


@pytest.fixture
def live_hub():
    """Function-scoped: a backend's HTTP client is bound to the event loop
    of the test that first used it."""
    if not load_env():
        pytest.skip(f"no gateway credentials in {CALLBOT_ENV}")
    import operonx
    from operonx.core.registry.resource_hub import ResourceHub

    ResourceHub.reset_instance()
    hub = operonx.bootstrap(resources=RESOURCES, env=False)
    yield hub
    ResourceHub.reset_instance()
