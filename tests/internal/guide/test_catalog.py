"""``.operonx/catalog.md``: generated from the project's source, rewritten only when it changes."""

from pathlib import Path

from operonx.guide import catalog

_OPS = '''from operonx import op, graph, Job


@op(retry=2)
def charge(amount: int, currency: str = "VND") -> dict:
    """Charge the card once. Retried on a timeout."""


@graph
def billing():
    """Charge, then mail the receipt."""


nightly = Job("nightly-billing", graph=billing, description="every night at 2")
'''


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "ops.py").write_text(_OPS)
    (tmp_path / "src" / "billing" / "tests").mkdir()
    (tmp_path / "src" / "billing" / "tests" / "helpers.py").write_text(
        "from operonx import op\n@op\ndef fake(): ...\n"
    )
    (tmp_path / "src" / "broken.py").write_text("def (:\n")
    return tmp_path


def test_the_catalog_lists_ops_graphs_and_jobs(tmp_path):
    text = catalog.render(_project(tmp_path))
    assert (
        "- `charge(amount: int, currency: str='VND') -> dict` · src/billing/ops.py:5 · Charge the card once."
        in text
    )
    assert "- `billing()` · src/billing/ops.py:10 · Charge, then mail the receipt." in text
    assert "- `nightly-billing` runs `billing` · src/billing/ops.py:14 · every night at 2" in text
    assert "fake" not in text and "## Services" not in text


def test_write_only_when_it_changed(tmp_path):
    root = _project(tmp_path)
    assert catalog.write(root) is True
    assert (root / catalog.PATH).read_text().startswith("# Catalog")
    assert catalog.write(root) is False
    (root / "src" / "billing" / "ops.py").write_text(
        _OPS + '\n\n@op\ndef refund():\n    """Give it back."""\n'
    )
    assert catalog.write(root) is True
    assert "refund()" in (root / catalog.PATH).read_text()
