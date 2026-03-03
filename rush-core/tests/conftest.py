"""Shared fixtures for rush-core tests."""

import json
import os

from rush_core import Rush

# Path to the built-in ops crate (auto-built by the plugin loader)
BUILTIN_CRATE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "examples", "rush-ops-builtin")
)


def make_rush(config: dict) -> Rush:
    """Create a Rush engine from a config dict (handles JSON serialization)."""
    return Rush(json.dumps(config, default=str))
