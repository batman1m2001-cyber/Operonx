"""Shared fixtures: golden-update flag, a tmp ResourceHub, golden paths."""

from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite golden snapshots instead of comparing (review the diff after)",
    )


@pytest.fixture
def update_golden(request) -> bool:
    return bool(request.config.getoption("--update-golden"))
