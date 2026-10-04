"""Fixtures shared by the eval tests."""

from __future__ import annotations

import pytest

from tests.internal.app.evals._fake_llm import fake_llm, llm_hub
from tests.internal.app.evals._flows import CALLS


@pytest.fixture
def llm(tmp_path):
    """``llm:bot`` answers from a local stand-in for the test's duration."""
    from operonx.core.registry import ResourceHub

    CALLS.clear()
    with fake_llm() as server:
        llm_hub(tmp_path, server.base_url)
        try:
            yield server.base_url
        finally:
            ResourceHub.reset_instance()
