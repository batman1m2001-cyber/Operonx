"""Every snippet in the core guide (operonx/guide/*.md) runs.

The guide is read by coding assistants that copy what it shows, so a
snippet that does not run is a bug. The harness is
`operonx.guide.testing`, which operonx-agents and operonx-kb use for their
own guides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operonx.guide import pages
from operonx.guide.testing import requires, run_page

# One child interpreter per snippet: about a minute. Run with -m slow.
pytestmark = pytest.mark.slow

PAGES = pages()


@pytest.mark.parametrize("page", PAGES, ids=[p.name for p in PAGES])
def test_every_snippet_on_the_page_runs(page: Path, tmp_path: Path, fake_llm):
    for module in requires(page):
        pytest.importorskip(module, reason=f"{page.name} needs {module}")
    ran = run_page(page, tmp_path, llm_url=fake_llm)
    assert ran or page.name == "README.md", f"{page.name} has no runnable snippet"
