"""Every snippet in this package's guide (operonx_project/guide/*.md) runs."""

from __future__ import annotations

from pathlib import Path

import pytest
from operonx.guide.testing import run_page

GUIDE = Path(__file__).resolve().parents[1] / "operonx_project" / "guide"
PAGES = sorted(GUIDE.glob("[0-9]*.md"))


@pytest.mark.slow  # one child interpreter per snippet
@pytest.mark.parametrize("page", PAGES, ids=[p.name for p in PAGES])
def test_every_snippet_on_the_page_runs(page: Path, tmp_path: Path):
    assert run_page(page, tmp_path), f"{page.name} has no runnable snippet"


def test_the_guide_is_registered_for_operonx_guide():
    from importlib.metadata import entry_points

    assert any(e.name == "project" and e.value == "operonx_project.guide" for e in entry_points(group="operonx.guides"))
