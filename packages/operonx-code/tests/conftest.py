"""Shared fixtures. The registry is process-wide, so tests that build an
agent must not leak tools into tests that count them."""

import pytest
from operonx_code.tools import register_all

from operonx.agents import clear_registry


@pytest.fixture(autouse=True)
def _registry():
    clear_registry()
    register_all()
    yield
    clear_registry()
