# K1b: ML layout vs. the heuristic

Measured 2026-10-04 with `scripts/eval_layout.py --threads 4 --pages 20 --docling-tests <docling>/tests/data/pdf`,
CPU only (Xeon E5-2686 v4, 4 torch threads), docling Heron (`docling-project/docling-layout-heron`
@ 8f39ad3) and TableFormer accurate (`docling-project/docling-models` v2.3.0). Model load and the
first parse take 13.7 s and are excluded from the per-page times. Weights are downloaded from
Hugging Face on first use (171 MB + 358 MB; Apache-2.0 / CDLA-Permissive-2.0).

Two references, each with a bias that has to be read with the numbers:

1. **Golden PDFs** (`tests/golden/truth`, written by hand from what the generators draw). The
   heuristic was developed against these three documents, so it is expected to be perfect on them.
2. **docling's own test PDFs** (17 real documents, 97 pages: arXiv papers, a newspaper, a manual,
   an IBM Redbook, right-to-left samples). The reference is docling's *output* for them, produced
   by the same layout model, so this measures agreement with docling and favours the model.
   The files are not copied into this repo; pass a docling checkout's path.

Metrics (see `operonx_kb/testing/layout_eval.py`): text recall = share of reference blocks found
(text similarity ≥ 0.9); kind accuracy = found with the right kind; order = consecutive found
pairs in order; table cells = cells equal at the same row and column; spurious = blocks matching
nothing.

| file | layout | text recall | kind acc. | heading level acc. | order | table cells | spurious | s/page |
|---|---|---|---|---|---|---|---|---|
| chinh_sach_vi.pdf | heuristic | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 | 0.019 |
| chinh_sach_vi.pdf | model | 1.00 | 0.70 | 1.00 | 1.00 | 1.00 | 0 | 1.665 |
| table_report.pdf | heuristic | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 | 0.016 |
| table_report.pdf | model | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 | 3.952 |
| two_column_report.pdf | heuristic | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 | 0.033 |
| two_column_report.pdf | model | 1.00 | 0.96 | 1.00 | 0.96 | 1.00 | 0 | 2.461 |

| docling reference | layout | pages | text recall | kind acc. | order | table cells | spurious | s/page |
|---|---|---|---|---|---|---|---|---|
| 2203.01017v2.pdf | heuristic | 16 | 0.69 | 0.64 | 0.86 | 0.11 | 114 | 0.189 |
| 2203.01017v2.pdf | model | 16 | 0.97 | 0.84 | 0.89 | 0.83 | 53 | 3.567 |
| 2206.01062.pdf | heuristic | 9 | 0.60 | 0.47 | 0.95 | 0.00 | 82 | 0.215 |
| 2206.01062.pdf | model | 9 | 0.90 | 0.89 | 0.97 | 0.96 | 26 | 5.159 |
| 2305.03393v1-pg9.pdf | heuristic | 1 | 0.80 | 0.50 | 1.00 | 0.00 | 1 | 0.053 |
| 2305.03393v1-pg9.pdf | model | 1 | 1.00 | 1.00 | 1.00 | 0.85 | 0 | 3.861 |
| 2305.03393v1.pdf | heuristic | 14 | 0.45 | 0.37 | 0.91 | 0.13 | 168 | 0.090 |
| 2305.03393v1.pdf | model | 14 | 0.97 | 0.96 | 0.95 | 0.82 | 12 | 2.818 |
| amt_handbook_sample.pdf | heuristic | 1 | 0.73 | 0.53 | 1.00 | 1.00 | 12 | 0.432 |
| amt_handbook_sample.pdf | model | 1 | 1.00 | 1.00 | 0.93 | 1.00 | 2 | 1.429 |
| code_and_formula.pdf | heuristic | 2 | 0.60 | 0.47 | 0.88 | 1.00 | 4 | 0.040 |
| code_and_formula.pdf | model | 2 | 1.00 | 1.00 | 0.86 | 1.00 | 1 | 1.696 |
| elsevier-00.pdf | heuristic | 19 | 0.39 | 0.35 | 0.84 | 0.00 | 129 | 0.184 |
| elsevier-00.pdf | model | 19 | 0.98 | 0.97 | 0.86 | 0.89 | 21 | 4.649 |
| multi_page.pdf | heuristic | 5 | 0.70 | 0.70 | 1.00 | 1.00 | 22 | 0.037 |
| multi_page.pdf | model | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 0 | 2.737 |
| newspaper-00.pdf | heuristic | 1 | 0.19 | 0.11 | 0.78 | 1.00 | 107 | 0.250 |
| newspaper-00.pdf | model | 1 | 0.77 | 0.75 | 0.80 | 1.00 | 24 | 1.902 |
| normal_4pages.pdf | heuristic | 4 | 0.34 | 0.16 | 0.89 | 1.00 | 45 | 0.061 |
| normal_4pages.pdf | model | 4 | 0.90 | 0.89 | 0.96 | 1.00 | 17 | 3.625 |
| picture_classification.pdf | heuristic | 2 | 1.00 | 0.67 | 1.00 | 1.00 | 2 | 0.025 |
| picture_classification.pdf | model | 2 | 1.00 | 0.89 | 0.75 | 1.00 | 2 | 2.034 |
| redp5110_sampled.pdf | heuristic | 18 | 0.67 | 0.57 | 0.87 | 0.57 | 113 | 0.040 |
| redp5110_sampled.pdf | model | 18 | 0.96 | 0.95 | 0.89 | 0.55 | 27 | 3.640 |
| right_to_left_01.pdf | heuristic | 1 | 0.00 | 0.00 | 1.00 | 1.00 | 11 | 0.037 |
| right_to_left_01.pdf | model | 1 | 0.00 | 0.00 | 1.00 | 1.00 | 3 | 1.154 |
| right_to_left_02.pdf | heuristic | 1 | 0.25 | 0.00 | 1.00 | 1.00 | 2 | 0.050 |
| right_to_left_02.pdf | model | 1 | 0.25 | 0.25 | 1.00 | 1.00 | 4 | 1.319 |
| right_to_left_03.pdf | heuristic | 1 | 0.06 | 0.06 | 1.00 | 0.00 | 5 | 0.065 |
| right_to_left_03.pdf | model | 1 | 0.31 | 0.31 | 0.50 | 0.44 | 13 | 2.384 |
| table_misidentified_as_form.pdf | heuristic | 1 | 0.43 | 0.16 | 0.76 | 0.00 | 49 | 0.043 |
| table_misidentified_as_form.pdf | model | 1 | 0.88 | 0.86 | 0.70 | 1.00 | 18 | 4.039 |
| table_mislabeled_as_picture.pdf | heuristic | 1 | 0.06 | 0.06 | 1.00 | 0.00 | 1 | 0.067 |
| table_mislabeled_as_picture.pdf | model | 1 | 0.85 | 0.79 | 0.93 | 0.63 | 8 | 5.199 |
| **all (97 pages)** | heuristic | 97 | 0.51 | 0.42 | | | 867 | 0.124 |
| **all (97 pages)** | model | 97 | 0.93 | 0.90 | | | 231 | 3.647 |
| generated 21p | heuristic | | | | | | | 0.090 |
| generated 21p | model | | | | | | | 3.047 |



## Reading

- **Golden PDFs:** both find every block and every table cell. The model's misses are in kind: it
  reads the Vietnamese numbered list as paragraphs (kind accuracy 0.70) and the last-page
  footnote as a page footer. Speed is 0.02 s/page for the heuristic and 1.7–4.0 s/page for the model.
- **Real PDFs:** the model agrees with docling on 0.93 of the blocks (0.90 with the right kind).
  The heuristic reaches only 0.51 (0.42), with 867 spurious blocks against the model's 231.
  The heuristic splits and joins blocks differently on dense academic layouts (affiliations, figure
  text, author blocks) and finds almost no borderless tables (table cells 0.00–0.13 on the papers).
  Part of that gap is the bias described above, but the spurious counts and the table scores show a
  real quality gap on documents like these.
- **Speed:** the model runs at 3.6 s/page on CPU against the heuristic's 0.12 s/page, about 30x
  slower. TableFormer adds about 1 s for each table.

## Decision

The heuristic stays the default (PLAN D3: no torch in the core install, and it is exact on the
simple born-digital documents it targets). For real-world PDFs, `PdfParser(layout=ModelLayout())`
is the recommended setting, from the `layout` extra. Improving the heuristic on academic layouts
(author blocks, figure-internal text, borderless tables) is a measured follow-up that this script
gates. Right-to-left text is weak for both: neither reorders RTL runs.
