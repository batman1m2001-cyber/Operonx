"""K0 gate: the span invariant on every golden block fixture, plus tree snapshots."""

import json

import pytest
from conftest import GOLDEN

from operonx_kb.parsing.base import ParsedDoc
from operonx_kb.structure.build import build_version
from operonx_kb.testing.golden import compare_or_update, tree_snapshot
from operonx_kb.text.spans import check_elements

FIXTURES = sorted((GOLDEN / "blocks").glob("*.json"))


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_block_fixture(path, update_golden):
    parsed = ParsedDoc.model_validate(json.loads(path.read_text(encoding="utf-8")))
    tree = build_version(parsed, "ver_golden")
    assert check_elements(tree.canonical, tree.elements) == len(tree.elements)
    compare_or_update(
        tree_snapshot(tree), GOLDEN / "expected" / f"blocks_{path.stem}.json", update_golden
    )


def test_there_are_golden_block_fixtures():
    assert len(FIXTURES) >= 4
