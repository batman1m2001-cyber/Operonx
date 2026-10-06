"""K1d gate: the default layout stays exact on the golden PDFs' hand-written truth."""

import importlib.util
from pathlib import Path

import pytest

from operonx_kb.testing.layout_eval import load_truth, score_layout

GOLDEN = Path(__file__).parent
TRUTHS = sorted((GOLDEN / "truth").glob("*.json"))

pytestmark = [
    pytest.mark.pdf,
    pytest.mark.skipif(
        importlib.util.find_spec("docling_parse") is None, reason="needs the 'pdf' extra"
    ),
]


@pytest.mark.parametrize("truth_file", TRUTHS, ids=[p.stem for p in TRUTHS])
def test_heuristic_layout_is_exact_on_golden_truth(truth_file: Path):
    from operonx_kb.pdf.parser import PdfParser

    name = truth_file.name[: -len(".json")]
    doc = PdfParser().parse((GOLDEN / "docs" / name).read_bytes())
    s = score_layout(load_truth(truth_file), doc.blocks)
    scores = {
        k: s[k] for k in ("text_recall", "kind_accuracy", "level_accuracy", "order", "table_cells")
    }
    assert scores == {k: 1.0 for k in scores} and s["spurious"] == 0, s["mismatches"]
