"""Every snippet in this package's guide (operonx_agents/guide/*.md) runs."""

from __future__ import annotations

from pathlib import Path

import pytest
from operonx.guide.testing import run_page, stand_in_model

# One child interpreter per snippet. Run with -m slow.
pytestmark = pytest.mark.slow

GUIDE = Path(__file__).resolve().parents[1] / "operonx_agents" / "guide"
PAGES = sorted(GUIDE.glob("[0-9]*.md"))


@pytest.mark.parametrize("page", PAGES, ids=[p.name for p in PAGES])
def test_every_snippet_on_the_page_runs(page: Path, tmp_path: Path):
    with stand_in_model() as url:
        assert run_page(page, tmp_path, llm_url=url), f"{page.name} has no runnable snippet"
