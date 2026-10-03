"""The ML layout seam (extra `layout`), with fake models: no weights needed."""

import importlib.util
from pathlib import Path

import pytest

from operonx_kb.pdf.backend import Word
from operonx_kb.pdf.models import (
    Detection,
    LayoutDetector,
    ModelLayout,
    TableStructurer,
    postprocess,
)

DOCS = Path(__file__).parents[1] / "golden" / "docs"
needs_render = pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None
    or importlib.util.find_spec("docling_parse") is None,
    reason="needs the 'layout' extra",
)


def _w(text, x0, y0, x1, y1):
    return Word(text, x0, y0, x1, y1, "F", y1 - y0)


def test_low_confidence_regions_are_dropped_and_title_reads_as_header():
    dets = [Detection("text", (0, 0, 100, 10), 0.49), Detection("title", (0, 20, 100, 30), 0.46)]
    kept, orphans = postprocess(dets, [_w("a", 1, 1, 9, 9), _w("T", 1, 21, 9, 29)])
    assert [(d.label, [w.text for w in d.words]) for d in kept] == [("section_header", ["T"])]
    assert [w.text for w in orphans] == ["a"]


def test_overlapping_regions_keep_one_winner():
    box = (0, 0, 100, 10)
    dets = [
        Detection("caption", box, 0.7),
        Detection("text", box, 0.54),
        Detection("section_header", box, 0.45),
    ]
    kept, _ = postprocess(dets, [_w("Table", 1, 1, 30, 9)])
    assert [d.label for d in kept] == ["caption"]
    # docling's rule 1: text of about the same area cannot disqualify a list item,
    # however much more confident; without it the 0.06 gap would.
    group = [Detection("list_item", box, 0.8), Detection("text", (0, 0, 98, 10), 0.86)]
    kept, _ = postprocess(group, [_w("x", 1, 1, 9, 9)])
    assert [d.label for d in kept] == ["list_item"]


def test_wrappers_lose_to_tables_and_text_inside_a_table_belongs_to_it():
    words = [_w("a", 5, 5, 15, 15), _w("b", 50, 5, 60, 15)]
    dets = [
        Detection("table", (0, 0, 100, 20), 0.8),
        Detection("key_value_region", (0, 0, 100, 20), 0.55),
        Detection("text", (4, 4, 16, 16), 0.7),
    ]
    kept, orphans = postprocess(dets, words)
    assert (
        [d.label for d in kept] == ["table"]
        and [w.text for w in kept[0].words] == ["a", "b"]
        and not orphans
    )


class FakeDetector(LayoutDetector):
    name = "fake"

    def __init__(self, regions):
        self.regions = regions

    def detect(self, images, scales):
        return [
            [Detection(label, bbox, score) for label, bbox, score in self.regions.get(i + 1, [])]
            for i in range(len(images))
        ]


class FakeTables(TableStructurer):
    name = "fake-tables"
    scale = 1.0

    def structure(self, image, page, bbox, words):
        return [["cells", str(len(words))]]


@needs_render
def test_model_layout_runs_through_the_pdf_parser_with_page_images():
    from operonx_kb.pdf.parser import PdfParser

    regions = {1: [("section_header", (50, 40, 300, 70), 0.9), ("table", (50, 125, 510, 205), 0.95),
                   ("caption", (50, 112, 240, 127), 0.8)]}  # fmt: skip
    layout = ModelLayout(detector=FakeDetector(regions), tables=FakeTables())
    doc = PdfParser(layout=layout).parse((DOCS / "table_report.pdf").read_bytes())
    kinds = [b.kind for b in doc.blocks]
    assert kinds[:3] == [
        "title",
        "paragraph",
        "caption",
    ]  # orphan words become paragraphs, in order
    table = next(b for b in doc.blocks if b.kind == "table")
    assert table.attrs["rows"][0][0] == "cells" and int(table.attrs["rows"][0][1]) > 10
    assert (
        layout.fingerprint()
        != ModelLayout(
            detector=FakeDetector({}), tables=FakeTables(), segment_gap=2.0
        ).fingerprint()
    )


def test_model_layout_refuses_to_run_without_images():
    with pytest.raises(ValueError, match="page images"):
        ModelLayout(detector=FakeDetector({}), tables=FakeTables()).layout([])
