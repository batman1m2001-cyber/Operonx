"""``brd check`` against OneWealth's own checker, on a copy of its folder.

Set ``ONEWEALTH_COPY`` to a copy of the OneWealth repo (its ``docs/``,
``tests/``, ``scripts/check_brd_docs.py`` and the code the docs link to;
the private BRD at ``private/brd.docx`` when there is one). The copy is
never the live repo: these tests write planted faults into a scratch tree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from operonx_project.brd.checker import check

COPY = os.environ.get("ONEWEALTH_COPY")
CONFIG = Path(__file__).parent / "data" / "onewealth_brd.toml"
pytestmark = pytest.mark.skipif(not COPY or not Path(COPY, "docs/brd").is_dir(), reason="ONEWEALTH_COPY not set")


def _tree(tmp: Path) -> Path:
    """docs/ copied (faults are planted there); the rest linked."""
    src = Path(COPY)
    root = tmp / "ow"
    root.mkdir()
    shutil.copytree(src / "docs", root / "docs")
    for name in os.listdir(src):
        if name != "docs" and not (root / name).exists():
            (root / name).symlink_to(src / name)
    shutil.copy(CONFIG, root / "docs" / "brd" / "brd.toml")
    return root


def _theirs(root: Path):
    brd = root / "private" / "brd.docx"
    args = [sys.executable, str(root / "scripts" / "check_brd_docs.py")] + (["--brd", str(brd)] if brd.exists() else [])
    # their checker finds the repo from its own path: run a copy of it inside the tree
    script = root / "scripts_local" / "check_brd_docs.py"
    script.parent.mkdir(exist_ok=True)
    shutil.copy(root / "scripts" / "check_brd_docs.py", script)
    args[1] = str(script)
    p = subprocess.run(args, capture_output=True, text=True, cwd=root)
    errors = [ln[len("ERROR ") :] for ln in p.stdout.splitlines() if ln.startswith("ERROR ")]
    report = [ln.strip() for ln in p.stdout.splitlines() if ln.startswith("  ")]
    return errors, report


def _ours(root: Path):
    brd = root / "private" / "brd.docx"
    return check(root / "docs" / "brd", {"SRC-01": brd} if brd.exists() else None)


def test_agrees_on_the_real_folder(tmp_path):
    root = _tree(tmp_path)
    theirs, their_report = _theirs(root)
    ours, our_report = _ours(root)
    assert sorted(ours) == sorted(theirs)
    assert our_report == their_report


def test_agrees_on_planted_faults(tmp_path):
    root = _tree(tmp_path)
    brd = root / "docs" / "brd"
    m = brd / "requirements" / "requirements_traceability.md"
    text = m.read_text(encoding="utf-8")
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith("| BRD-BR-02 |"))
    del lines[i]  # 1. a defined requirement loses its row (and the summary no longer adds up)
    j = next(n for n, ln in enumerate(lines) if ln.startswith("| BRD-BR-03 |"))
    lines[j] = lines[j].replace("implemented_verified", "implemented_verifed")  # 2. a status typo
    lines.append("See tests/test_news.py::test_that_does_not_exist and Q-99 and FEAT-99.")  # 3, 4, 5
    m.write_text("\n".join(lines) + "\n", encoding="utf-8")
    readme = brd / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\n[gone](nowhere.md) [anchor](README.md#no-such)\n")  # 6
    kit = next((brd / "sources").glob("redesign_kit_*/README.md"))
    kit.write_text(kit.read_text(encoding="utf-8") + "edited\n")  # 7. a verbatim copy changed
    (brd / "assets" / "stray.png").write_bytes(b"\x89PNG")  # 8. an unregistered asset
    theirs, _ = _theirs(root)
    ours, _ = _ours(root)
    assert len(theirs) >= 9
    assert sorted(ours) == sorted(theirs)
