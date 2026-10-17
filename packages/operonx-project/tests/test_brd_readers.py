"""Readers on real files: a .docx with a table and a figure, a scanned PDF page, a zip with an image."""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

import pytest

from operonx_project.brd.extraction import find_quote
from operonx_project.brd.readers import ReadError, normalize, read_source
from tests.brd_files import SAMPLE_BLOCKS, make_docx, make_scan_pdf, make_text_png, make_zip


def test_docx_sections_tables_and_figures(tmp_path):
    src = read_source(make_docx(tmp_path / "brd.docx", SAMPLE_BLOCKS), tmp_path / "out")
    assert src.kind == "docx" and len(src.sha256) == 64
    by_id = {s.id: s for s in src.sections}
    assert {"§1", "§2", "§3"} <= set(by_id)
    assert by_id["§2"].location == "§2 Yêu cầu kinh doanh"
    assert "BR-01 | Gửi lời nhắc trước buổi học 24 giờ" in by_id["§2"].text  # a table row
    assert by_id["§3"].path == ["Đặc tả hệ thống nhắc lịch", "3. Quy tắc"]
    (fig,) = src.figures
    assert Path(fig).read_bytes()[:4] == b"\x89PNG" and by_id["§3"].figures == [fig]


def test_a_scanned_pdf_page_is_read_by_ocr(tmp_path):
    src = read_source(make_scan_pdf(tmp_path / "scan.pdf", ["Requirement: reminders go out a day ahead.",
                                                            "Yêu cầu: gửi nhắc lịch trước 24 giờ."]))  # fmt: skip
    assert src.kind == "pdf" and src.pages == 1 and src.ocr_pages == [1]
    assert all(s.ocr for s in src.sections)
    assert "reminders go out a day ahead" in normalize(src.text)
    # OCR misses accents: a correct quote still matches an OCR'd section, loosely
    assert find_quote("gửi nhắc lịch trước 24 giờ", src.sections) is not None


def test_a_zip_kit_with_an_image(tmp_path):
    png = make_text_png(tmp_path / "m.png", ["Mockup: the Today screen", "lists reminders due"])
    z = make_zip(tmp_path / "kit.zip", {"kit/README.md": "# Kit\n\nThe screen must show a reminder count.\n",
                                       "kit/mock.png": png.read_bytes(), "__MACOSX/._x": b"junk"})  # fmt: skip
    src = read_source(z, tmp_path / "kit")
    locs = [s.location for s in src.all_sections()]
    assert locs[0].startswith("kit/README.md") and locs[1].startswith("kit/mock.png")
    assert "today screen" in normalize(src.text) and "reminder count" in normalize(src.text)
    assert not (tmp_path / "kit" / "files" / "__MACOSX").exists()


@pytest.mark.parametrize("name", ["../evil.txt", "/abs.txt"])
def test_a_zip_cannot_write_outside_its_folder(tmp_path, name):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr(name, "x")
    with pytest.raises(ReadError, match="outside the folder"):
        read_source(z, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_a_zip_goes_only_into_an_empty_folder(tmp_path):
    z = make_zip(tmp_path / "k.zip", {"a.md": "# A\n"})
    (tmp_path / "out" / "files").mkdir(parents=True)
    (tmp_path / "out" / "files" / "old").write_text("x")
    with pytest.raises(ReadError, match="not empty"):
        read_source(z, tmp_path / "out")


def test_markdown_and_text(tmp_path):
    md = tmp_path / "goal.md"
    md.write_text("# Goal\n\nFlag office documents in commits.\n\n## Scope\n\nOnly .docx and .xlsx.\n")
    src = read_source(md)
    assert [s.title for s in src.sections] == ["Goal", "Scope"]


BRD = os.environ.get("ONEWEALTH_COPY")


@pytest.mark.skipif(not BRD or not Path(BRD, "private/brd.docx").exists(), reason="no private BRD copy")
def test_onewealths_brd(tmp_path):
    src = read_source(Path(BRD, "private/brd.docx"), tmp_path / "out")
    assert len(src.figures) == 5
    assert len(set(re.findall(r"\b(BR-\d{2}|FR-\d{3}|NFR-\d{2})\b", src.text))) == 61
