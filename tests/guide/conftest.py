"""The stand-in model the guide's snippets talk to (operonx.guide.testing)."""

from __future__ import annotations

import pytest

from operonx.guide.testing import stand_in_model


@pytest.fixture(scope="session")
def fake_llm():
    with stand_in_model() as url:
        yield url
