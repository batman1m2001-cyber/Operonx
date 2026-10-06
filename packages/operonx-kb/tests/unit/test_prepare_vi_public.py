"""The D5 set builder on a tiny MLQA-shaped archive (no network)."""

import importlib.util
import json
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "prepare_vi_public.py"


def _module():
    spec = importlib.util.spec_from_file_location("prepare_vi_public", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _archive(path: Path) -> zipfile.ZipFile:
    context = "Hà Nội là thủ đô. Thành phố có 30 quận và huyện. Sông Hồng chảy qua."
    dev = {
        "data": [
            {
                "title": "Hà Nội",
                "paragraphs": [
                    {
                        "context": context,
                        "qas": [
                            {
                                "id": "q1",
                                "question": "Hà Nội có bao nhiêu quận huyện?",
                                "answers": [{"text": "30", "answer_start": context.index("30")}],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    test = {"data": [{"title": "Hà Nội", "paragraphs": [{"context": context, "qas": []}]},
                     {"title": "Huế", "paragraphs": [{"context": "Huế là cố đô.", "qas": []}]}]}  # fmt: skip
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("MLQA_V1/dev/dev-context-vi-question-vi.json", json.dumps(dev))
        z.writestr("MLQA_V1/test/test-context-vi-question-vi.json", json.dumps(test))
    return zipfile.ZipFile(path)


def test_cases_quote_the_sentence_holding_the_human_answer(tmp_path):
    mod = _module()
    cases = mod.wiki_documents(tmp_path / "out", _archive(tmp_path / "m.zip"), distractors=5)
    (case,) = cases
    label = case["expected"]["relevant"][0]
    assert label == {"doc_key": "wiki_0000_h_ni.html", "quote": "Thành phố có 30 quận và huyện."}
    assert case["expected"]["answer"] == "30" and case["input"]["collection"] == "vi_public"
    files = sorted(p.name for p in (tmp_path / "out" / "corpus").iterdir())
    # the dev article, and only the test article that is not in dev (a distractor, no case)
    assert files == ["wiki_0000_h_ni.html", "wikix_0000_hu.html"]


def test_the_legal_manifest_lists_text_pdfs_with_hashes():
    mod = _module()
    rows = [json.loads(line) for line in mod.LEGAL_MANIFEST.read_text("utf-8").splitlines()]
    assert len(rows) >= 50
    for row in rows:
        assert row["url"].startswith("https://datafiles.chinhphu.vn/") and row["file"].endswith(
            ".pdf"
        )
        assert len(row["sha256"]) == 64 and row["pages"] >= 1
    assert len({r["sha256"] for r in rows}) == len(rows)
