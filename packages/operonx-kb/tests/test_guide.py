"""Every snippet in this package's guide (operonx_kb/guide/*.md) runs."""

from __future__ import annotations

from pathlib import Path

import pytest
from operonx.guide.testing import run_page

# One child interpreter per snippet. Run with -m slow.
pytestmark = pytest.mark.slow

GUIDE = Path(__file__).resolve().parents[1] / "operonx_kb" / "guide"
PAGES = sorted(GUIDE.glob("[0-9]*.md"))


@pytest.mark.parametrize("page", PAGES, ids=[p.name for p in PAGES])
def test_every_snippet_on_the_page_runs(page: Path, tmp_path: Path):
    pytest.importorskip("faiss", reason="the guide's vector store is FAISS (extra `faiss`)")
    assert run_page(page, tmp_path), f"{page.name} has no runnable snippet"
