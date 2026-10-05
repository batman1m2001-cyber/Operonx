"""Fixtures every test directory shares."""

from __future__ import annotations

import logging

import pytest


@pytest.fixture
def caplog(caplog):
    """pytest's ``caplog``, hearing operonx too: operonx's logger does not
    propagate to the root logger, where ``caplog`` listens, so a test
    asserting on an operonx warning saw nothing."""
    logger = logging.getLogger("operonx.core")
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
