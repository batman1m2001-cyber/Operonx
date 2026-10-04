# K1d: a better heuristic layout, measured against a hand-checked reference

Measured 2026-10-04 on branch `layout-heuristic`, CPU only (Xeon E5-2686 v4; the model with 4 torch
threads), with

    PYTHONPATH=<operonx feat-kb-upstream> uv run python scripts/eval_layout.py \
        --docling-tests <docling @ 51fe9ff>/tests/data/pdf --json scores.json
    PYTHONPATH=<operonx feat-kb-upstream> uv run python scripts/diagnose_layout.py \
        --docling-tests <docling @ 51fe9ff>/tests/data/pdf

"Before" is master at `8af96d2` (the K1b state) scored by the same scripts.

## 1 · The measurement

K1b scored real pages only against docling's *output* for its own test PDFs: a model reference,
which rewards agreeing with that model. K1d adds a reference that is not a model and fixes two
biases of the scorer.

**Hand reference** (`tests/layout_reference/reference.json`): 12 real pages, every block decided by
looking at the rendered page, block texts taken from the PDF text layer, reasoning written per page
and per ambiguous block, conventions at the top of the file (blocks, reading order, continued
paragraphs, list items, headings, furniture, table grids, figures, scope).

| page | genre | source, license |
|---|---|---|
| paper-titlepage | ACM two-column title page (authors grid, arXiv stamp, figure) | arXiv 2206.01062 p1, docling test set |
| paper-table | LNCS page with a ruled table with spans | arXiv 2305.03393 p9, docling test set |
| paper-twocolumn | CVPR body page, borderless table, paragraph across columns | arXiv 2203.01017 p4, docling test set |
| report-redbook | technical report, bullets in a symbol font | IBM Redpaper REDP-5110, docling test set |
| manual-twocolumn | FAA handbook, two columns, figures | public domain, docling test set |
| newspaper-editorial | newspaper, narrow columns (scoped to one article) | 20 Minuten, docling test set |
| report-korean | Korean policy brief: masthead, two columns, footnotes | NARS, docling test set |
| code-listing | article with a code listing | docling test set |
| form-irs-w9 | IRS W-9 form, then two-column instructions | US government work, committed |
| slide-bullets | slide: subheadings, bullets, chart | NASA NTRS 20110014565, committed |
| slide-nested-list | slide: nested bullets, logos | NASA NTRS 20170004414, committed |
| encyclopedia-vietnamese | Vietnamese Wikipedia page beside an infobox | CC BY-SA 4.0, committed |

186 blocks (172 text and table blocks, 14 figures). Docling's PDFs are referenced by path and
sha256, not copied; the other four are committed as single-page PDFs. `test_layout_eval.py` checks
the file (pinned hashes, kinds) and that every word of every committed block is on its page.

**Scorer fixes.** docling's pictures used to vanish from its reference, so every correct figure was
"spurious"; they are now figures matched by geometry and kept out of text recall. Equal texts go to
the block of the same kind. A single page (or a scoped region) of a document can be scored. The K1b
headline of 867 spurious blocks is 808 under the fixed scorer, on the same output.

## 2 · Results

| reference | layout | text recall | kind acc. | table cells | figures | spurious | s/page |
|---|---|---|---|---|---|---|---|
| golden PDFs (3 docs) | heuristic before | 1.00 | 1.00 | 1.00 | | 0 | 0.02 |
| golden PDFs (3 docs) | heuristic after | 1.00 | 1.00 | 1.00 | | 0 | 0.03 |
| golden PDFs (3 docs) | model | 1.00 | 0.70–1.00 | 1.00 | | 0 | 1.6–4.1 |
| **hand (12 pages)** | heuristic before | 0.44 | 0.34 | 0.00 | 12/14 | 94 | 0.139 |
| **hand (12 pages)** | **heuristic after** | **0.69** | **0.58** | 0.00 | 12/14 | **78** | 0.134 |
| **hand (12 pages)** | model before | 0.93 | 0.84 | 0.26 | 14/14 | 59 | 2.43 |
| **hand (12 pages)** | model after | 0.90 | 0.82 | 0.26 | 14/14 | 60 | 2.57 |
| docling outputs (97 pages) | heuristic before | 0.51 | 0.42 | 0.13 | 59/77 | 808 | 0.123 |
| docling outputs (97 pages) | **heuristic after** | **0.72** | **0.63** | 0.13 | 59/77 | 869 | 0.150 |
| docling outputs (97 pages) | model before | 0.93 | 0.92 | 0.83 | 77/77 | 154 | 3.43 |
| docling outputs (97 pages) | model after | 0.94 | 0.92 | 0.83 | 77/77 | 152 | 3.61 |
| generated 21 pages | heuristic before / after | | | | | | 0.081 / 0.101 |

The golden gate (exact on all three golden PDFs) and the span invariant hold at every step; the
golden truth is now a test (`tests/golden/test_layout_truth.py`). The model rows changed because
`ModelLayout` shares the reading order and merges with the heuristic (§4).

### Step by step (heuristic)

| commit | change | hand recall / kind / spurious | docling recall / kind / spurious |
|---|---|---|---|
| `8af96d2` | before | 0.44 / 0.34 / 94 | 0.51 / 0.42 / 808 |
| `c5b72a4` | rotated text forms its own blocks | 0.47 / 0.37 / 85 | 0.52 / 0.43 / 803 |
| `76a0d7f` | bold/italic/mono from TeX, URW, Libertine font names | 0.48 / 0.41 / 87 | 0.52 / 0.45 / 818 |
| `5816cb3` | frames and figure panels are not tables; wrapped prose is not a borderless table | 0.54 / 0.44 / 105 | 0.55 / 0.47 / 913 |
| `9e180da` | geometric blocks, docling's rule-based reading order, leading-relative gaps, ink-measured fonts, justified lines, indent/run-in/marker rules, column-end merges | 0.67 / 0.53 / 92 | 0.70 / 0.60 / 920 |
| `7388edb` | furniture band, title, footnotes, dates, centred lines, set-apart bullets | 0.69 / 0.58 / 78 | 0.72 / 0.63 / 869 |

`5816cb3` raised spurious: text that false tables had swallowed reached the old column-flow block
builder, which split it; `9e180da` is the fix for that builder.

### Hand reference, per page (after)

| page | layout | text recall | kind acc. | order | table cells | figures | spurious | s/page |
|---|---|---|---|---|---|---|---|---|
| paper-titlepage | heuristic | 0.56 | 0.56 | 0.88 | | 1/1 | 18 | 0.283 |
| paper-titlepage | model | 0.88 | 0.88 | 0.85 | | 1/1 | 7 | 5.824 |
| paper-table | heuristic | 0.89 | 0.78 | 1.00 | 0.00 | | 2 | 0.061 |
| paper-table | model | 0.89 | 0.89 | 1.00 | 0.00 | | 2 | 4.357 |
| paper-twocolumn | heuristic | 0.77 | 0.77 | 0.89 | 0.00 | | 4 | 0.213 |
| paper-twocolumn | model | 1.00 | 1.00 | 0.92 | 1.00 | | 0 | 3.740 |
| report-redbook | heuristic | 1.00 | 0.83 | 0.91 | | | 3 | 0.065 |
| report-redbook | model | 1.00 | 1.00 | 0.91 | | | 0 | 3.488 |
| manual-twocolumn | heuristic | 0.93 | 0.71 | 0.92 | | 2/2 | 9 | 0.189 |
| manual-twocolumn | model | 1.00 | 1.00 | 0.92 | | 2/2 | 1 | 1.385 |
| newspaper-editorial | heuristic | 0.60 | 0.60 | 1.00 | | | 4 | 0.392 |
| newspaper-editorial | model | 0.80 | 0.80 | 1.00 | | | 3 | 1.713 |
| report-korean | heuristic | 0.44 | 0.31 | 0.83 | | 0/2 | 8 | 0.074 |
| report-korean | model | 0.81 | 0.69 | 0.83 | | 2/2 | 10 | 3.256 |
| code-listing | heuristic | 1.00 | 1.00 | 0.86 | | | 0 | 0.050 |
| code-listing | model | 1.00 | 1.00 | 0.86 | | | 0 | 1.729 |
| form-irs-w9 | heuristic | 0.46 | 0.37 | 0.87 | | | 7 | 0.149 |
| form-irs-w9 | model | 0.83 | 0.65 | 0.83 | | | 10 | 1.323 |
| slide-bullets | heuristic | 0.78 | 0.44 | 0.83 | | 3/3 | 6 | 0.025 |
| slide-bullets | model | 1.00 | 0.89 | 0.88 | | 3/3 | 0 | 1.375 |
| slide-nested-list | heuristic | 1.00 | 0.89 | 0.88 | | 3/3 | 2 | 0.014 |
| slide-nested-list | model | 1.00 | 0.89 | 0.88 | | 3/3 | 0 | 1.042 |
| encyclopedia-vietnamese | heuristic | 0.89 | 0.67 | 0.86 | 0.00 | 3/3 | 15 | 0.098 |
| encyclopedia-vietnamese | model | 0.89 | 0.78 | 0.86 | 0.00 | 3/3 | 27 | 1.638 |

Before, per page (heuristic): titlepage 0.31, table 0.89, twocolumn 0.62, redbook 1.00, manual
0.79, newspaper 0.00, korean 0.25, code 0.62, W-9 0.15, slides 0.89 and 0.11, Vietnamese 0.56.

### docling's outputs, per file (after)

| file | heuristic recall / kind / spurious | model recall / kind / spurious | heuristic before |
|---|---|---|---|
| 2203.01017v2 (16 p) | 0.88 / 0.82 / 128 | 0.96 / 0.91 / 42 | 0.69 / 0.64 / 101 |
| 2206.01062 (9 p) | 0.78 / 0.76 / 77 | 0.92 / 0.91 / 17 | 0.60 / 0.47 / 78 |
| 2305.03393v1-pg9 | 0.90 / 0.80 / 1 | 1.00 / 1.00 / 0 | 0.80 / 0.50 / 1 |
| 2305.03393v1 (14 p) | 0.87 / 0.82 / 146 | 0.97 / 0.96 / 6 | 0.45 / 0.37 / 163 |
| amt_handbook_sample | 0.87 / 0.67 / 9 | 1.00 / 1.00 / 0 | 0.73 / 0.53 / 10 |
| code_and_formula (2 p) | 1.00 / 0.93 / 1 | 1.00 / 1.00 / 1 | 0.60 / 0.47 / 4 |
| elsevier-00 (19 p) | 0.65 / 0.59 / 168 | 0.98 / 0.97 / 9 | 0.39 / 0.35 / 119 |
| multi_page (5 p) | 0.70 / 0.70 / 22 | 1.00 / 1.00 / 0 | 0.70 / 0.70 / 22 |
| newspaper-00 | 0.60 / 0.42 / 28 | 0.87 / 0.85 / 12 | 0.19 / 0.11 / 97 |
| normal_4pages (4 p) | 0.37 / 0.28 / 34 | 0.90 / 0.88 / 14 | 0.34 / 0.16 / 44 |
| picture_classification (2 p) | 1.00 / 0.89 / 0 | 1.00 / 0.89 / 0 | 1.00 / 0.67 / 0 |
| redp5110_sampled (18 p) | 0.77 / 0.64 / 139 | 0.97 / 0.96 / 10 | 0.67 / 0.57 / 102 |
| right_to_left_01/02/03 | 0.00, 0.25, 0.06 | 0.00, 0.25, 0.31 | 0.00, 0.25, 0.06 |
| table_misidentified_as_form | 0.59 / 0.27 / 72 | 0.90 / 0.88 / 14 | 0.43 / 0.16 / 49 |
| table_mislabeled_as_picture | 0.55 / 0.27 / 18 | 0.85 / 0.79 / 8 | 0.06 / 0.06 / 1 |

## 3 · Diagnosis: causes, before and after

`scripts/diagnose_layout.py` puts every unmatched block in one cause (definitions in its
docstring). Counts, heuristic layout:

| cause | hand before | hand after | docling before | docling after |
|---|---|---|---|---|
| spurious: piece (a reference block was split) | 37 | 38 | 297 | 239 |
| spurious: merge (reference blocks were joined) | 11 | 4 | 133 | 111 |
| spurious: table_text (part of a reference table we missed or mis-cut) | 3 | 13 | 122 | 229 |
| spurious: figure_text (text drawn inside a figure) | 3 | 2 | 90 | 173 |
| spurious: figure (a figure the reference does not have) | 17 | 12 | 66 | 51 |
| spurious: false_table | 10 | 2 | 10 | 0 |
| spurious: other | 13 | 7 | 90 | 66 |
| **spurious, total** | **94** | **78** | **808** | **869** |
| missed: joined into a larger block | 19 | 8 | 276 | 101 |
| missed: split into pieces | 36 | 23 | 181 | 178 |
| missed: swallowed by a predicted table | 25 | 13 | 64 | 9 |
| missed: table not matched | 3 | 3 | 22 | 22 |
| missed: text differs | 14 | 7 | 73 | 44 |
| **missed, total** | **97** | **54** | **616** | **354** |

Root causes found by reading those blocks on the rendered pages, in the order fixed (each fix has a
regression test replayed from a crop of the real page, `tests/unit/test_layout_real_crops.py`):

1. **Rotated words chained lines** (arXiv stamps, form side labels): their tall boxes overlapped
   every line they crossed. Rotated text now forms its own blocks; in the margin it is a page header.
2. **Bold went unseen** in TeX (CMBX, SFBX), URW (`-Medi`) and Libertine (`TB`) font names, so
   numbered headings became list items.
3. **False tables**: a box around a listing or a slide, an infobox title bar, Figure 1's grid of
   page thumbnails (1430 tiny words in an 8x9 "table"), narrow newspaper columns and side-by-side
   form labels read as borderless tables.
4. **Column-flow block building** (the largest): blocks joined whatever followed in a column-major
   order, so pages without a detectable gutter (newspapers, author grids, forms) joined
   side-by-side text, and a fixed gap threshold (0.5 of a box height) split newspaper paragraphs
   whose fonts give ink-tight boxes. Blocks are now built from geometric adjacency and ordered
   with docling's rule-based reading order; gaps are judged against the page's own leading;
   justified lines are no longer cut at their stretched spaces; first-line indents, run-in bold
   headings, centred lines and ragged-right list numbers are handled.
5. **Labels and furniture**: folios in a deep bottom margin, titles below a masthead line, small
   "1)" notes read as list items, dates read as enumerators, bullets set apart from their text.

## 4 · What is left, and what changed for the model

- **Tables (26% of the remaining docling spurious, table cells 0.00–0.13 on papers)**: borderless
  tables are found only by alignment and ruled grids split multi-line rows into one cell; cell
  structure needs either row/column projection inside a detected region or TableFormer.
- **Vector figures (20%)**: plots and diagrams drawn with paths are not detected, so their labels
  become paragraphs; docling-parse's shapes would allow a drawing-cluster detector.
- **Pieces that remain**: author blocks (name and affiliation differ in size), forms (the W-9 grid
  is a "table"), the Korean brief (half the reference blocks).
- **Right-to-left** text is still not reordered (neither layout does).
- `ModelLayout` now uses the same rule-based reading order and the column-end merge rule. At
  `2f48c21` that cost it 0.93 → 0.90 on the hand reference, all on the W-9 page (0.92 → 0.83):
  `diagnose_layout.py --layout model --page form-irs-w9` showed 4 merges and 7 joined misses,
  "C corporation S corporation Partnership Trust/estate" (checkbox labels on one row, each ending
  in a lower-case letter, each strictly right of the last: docling's merge test alone accepts
  them) and the rotated side label joined to "or". The old model path never merged them because
  gutter columns were equal on that page. Fixed in `layout-model-w9` for both layouts: on one
  page a continuation must start at least 1.5 lines above where the paragraph it continues ends
  (a column break goes up). Model: hand 0.93 / 0.85, docling 0.94 / 0.92 (§6).
- Speed: the heuristic stays CPU-cheap at 0.10–0.15 s/page (from 0.08–0.14); no Rust or ML needed.

## 5 · Recommendation

The heuristic stays the default (D3: no torch in core). It is now much closer on real documents —
text recall 0.69 on the hand reference and 0.72 against docling's outputs, from 0.44 and 0.51 — at
1/25 of the model's CPU time, but the model is still clearly better (0.90 / 0.94) and is the only
option with usable table structure. Recommend `PdfParser(layout=ModelLayout())` (extra `layout`)
for papers, forms and table-heavy PDFs; the heuristic is adequate for born-digital prose, reports,
manuals, slides and listings, and for bulk ingestion where 2.5–3.6 s/page is too slow.

## 6 · Follow-up: same-row merges (`layout-model-w9`)

| reference | layout | `2f48c21` recall / kind / spurious | after recall / kind / spurious |
|---|---|---|---|
| golden (3 docs) | heuristic | 1.00 / 1.00 / 0 | 1.00 / 1.00 / 0 |
| golden (3 docs) | model | 1.00 / 0.70–1.00 / 0 | 1.00 / 0.70–1.00 / 0 |
| hand (12 pages) | heuristic | 0.69 / 0.58 / 78 | 0.69 / 0.58 / 80 |
| hand (12 pages) | model | 0.90 / 0.82 / 60 | 0.93 / 0.85 / 59 |
| docling (97 pages) | heuristic | 0.72 / 0.63 / 869 | 0.72 / 0.63 / 880 |
| docling (97 pages) | model | 0.94 / 0.92 / 152 | 0.94 / 0.92 / 152 |

The heuristic finds the same blocks (docling: 1 more); its spurious count rises because merges
that were wrong in both versions are undone, leaving their pieces as separate unmatched blocks:
table header cells ("Model Dataset Simple TEDS Complex", "Train Test Val Simple Simple") and
figure labels ("Flexloc nut Elastic stop nut the most common ranges ...") that the old rule
chained across a row.
