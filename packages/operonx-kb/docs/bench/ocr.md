# OCR for scanned pages: `CollectionSpec.ocr` — gate

Measured 2026-10-06 with `scripts/bench_ocr.py` on D5's `vi_public` corpus (63 Vietnamese
legal PDFs, 970 pages), Tesseract 5.3.4 with `vie` data, 200 dpi, CPU, operonx-kb 0.2.3.
OCR runs only on a page whose text layer has fewer than `min_words` (3) words, and only
when the collection sets `ocr=OcrSpec(...)`; such a page is stored with `text_layer=False`.

## Verdict

**OCR ships opt-in.** It reads a scan into searchable, cited text
(`tests/graphs/test_ocr.py`: an image-only Vietnamese page, found by a lexical search on
its words, `pages == [1]`). On ordinary legal prose it is accurate enough to search
(median CER 4%). It is not accurate on diagrams and tables. A collection without scans
should leave it off: it costs about 6 s per page.

## Coverage: the corpus has no scans

Only 2 of 970 pages have no text layer. Both are blank last pages: 0.002% and 0.003% of
their pixels are dark. OCR returns nothing for them (0.8 s each), which is correct. D5's
answers therefore lost nothing to scans (`d5_review.md`), and coverage on real scans is
not measured here. The synthetic scan in the test is the evidence that the path works.

## Accuracy: OCR on pages that have a text layer

For each of 40 documents, the first page with a text layer was rendered and OCR'd. The
result was compared with that page's text layer: character error rate, whitespace
collapsed, plus a second rate with the diacritics removed.

| pages | n | median CER | median CER, no diacritics | CER < 5% | worst | s/page |
|---|---|---|---|---|---|---|
| legal prose (decrees, circulars) | 25 | **0.042** | 0.027 | 15 | 0.140 | 6.8 |
| annexes (diagrams, data tables) | 15 | 0.363 | 0.352 | 1 | 5.31 | 5.8 |
| all | 40 | 0.062 | 0.051 | 16 | 5.31 | 6.4 |

- **Diacritics:** on prose, about a third of the errors are tone marks (4.2% → 2.7%
  without them).
- **Annexes:** these are the forestry data annexes (`phuluc_*`: architecture diagrams,
  data dictionaries). Their CER mostly measures reading order, not misread letters:
  Tesseract reads boxes and cells in a different order from the text layer.
  - A CER above 1 means OCR emitted text the text layer does not have, such as text
    inside a diagram's images.
  - On a real scanned annex, expect retrieval to find the words but not the table's
    structure.

Reproduce: `uv run python scripts/bench_ocr.py <vi_public>/corpus` (writes `../ocr.json`).
