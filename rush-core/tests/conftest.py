"""Shared fixtures for rush-core tests."""

import pytest


@pytest.fixture
def linear_graph_data():
    """A -> B -> C linear chain."""
    return {
        "adjacency": {"A": [("B", False)], "B": [("C", False)], "C": []},
        "ready_count": {"A": 0, "B": 1, "C": 1},
        "entries": ["A"],
        "can_inline": {"A": True, "B": True, "C": True},
        "is_branch": {},
    }


@pytest.fixture
def parallel_fork_join_data():
    """A, B -> C (C waits for both)."""
    return {
        "adjacency": {"A": [("C", False)], "B": [("C", False)], "C": []},
        "ready_count": {"A": 0, "B": 0, "C": 2},
        "entries": ["A", "B"],
        "can_inline": {"A": True, "B": True, "C": True},
        "is_branch": {},
    }


@pytest.fixture
def diamond_graph_data():
    """A -> B, C -> D (D waits for both)."""
    return {
        "adjacency": {
            "A": [("B", False), ("C", False)],
            "B": [("D", False)],
            "C": [("D", False)],
            "D": [],
        },
        "ready_count": {"A": 0, "B": 1, "C": 1, "D": 2},
        "entries": ["A"],
        "can_inline": {"A": True, "B": True, "C": True, "D": True},
        "is_branch": {},
    }


@pytest.fixture
def branch_graph_data():
    """branch -> ~case_a, ~case_b; case_a > merge, case_b > merge."""
    return {
        "adjacency": {
            "branch": [("case_a", True), ("case_b", True)],
            "case_a": [("merge", True)],
            "case_b": [("merge", True)],
            "merge": [],
        },
        "ready_count": {"branch": 0, "case_a": 1, "case_b": 1, "merge": 1},
        "entries": ["branch"],
        "can_inline": {
            "branch": True,
            "case_a": True,
            "case_b": True,
            "merge": True,
        },
        "is_branch": {"branch": True},
    }
