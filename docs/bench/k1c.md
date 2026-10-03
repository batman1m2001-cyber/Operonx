# K1c gates: rebuild, catalogs, vector backends, 200-document corpus

Measured 2026-10-04 on the same machine as `k1_ingest.md`, operonx `feat/kb-upstream` c26b090,
Postgres 16 with pgvector (`pgvector/pgvector:pg16`) and Qdrant (`qdrant/qdrant:latest`) in
throwaway containers, HashEmbedder (dim 32).

| Gate | Test | Result |
|---|---|---|
| Unchanged re-ingest of a 200-document corpus (60 md, 50 html, 40 txt, 30 docx, 20 pdf; 40 Vietnamese) | `tests/graphs/test_corpus_gates.py::test_200_document_corpus` | 200 `skip`, **0** parse spans, **0** embed calls, 0 vectors written |
| Rebuild into a new index generation | same | id set identical; top-10 of **50** queries identical (scores, and ids above the 10th score); 0 parses, 0 embed calls (cache); old generation deleted |
| Purge | same | 0 vectors, 0 ledger rows, 0 catalog rows, raw and text blobs gone; `verify` clean |
| One-paragraph edit in a 106-page two-column PDF (1093 chunks) | `test_one_paragraph_edit_in_a_100_page_pdf` | **3** re-embeds, 3 vectors deleted, 1090 reused |
| Catalog conformance | `tests/conformance/test_catalog.py` | 9 tests × SQLite and Postgres, incl. two concurrent commits of one document |
| Vector backend conformance | `tests/conformance/test_vector_backends.py` | ingest / re-ingest / edit / purge / GC / rebuild on FAISS, pgvector (catalog in the same Postgres) and Qdrant |

The 200-document test (ingest 200, re-ingest 200, rebuild, purge) runs in 45 s; the 100-page edit
test in 23 s (two parses of 106 pages).

## Why the edit costs 3, not 1

Measured by diffing the chunks of both versions:

1. Before `136e939`, an oversize paragraph's sentence pieces were packed with neighbouring
   paragraphs, so the edit shifted chunk boundaries after it: **4** re-embeds. Pieces of an
   oversize element now form chunks of their own.
2. The remaining 2 are not churn in the new version but an error in the old one. The edit adds a
   line, so every later column break moves. In the old version one break fell exactly after a
   sentence-ending line, so docling's merge rule (the text breaks off mid-sentence) could not see
   that the paragraph went on, and it was split into two blocks. In the new version that break
   falls mid-sentence and the paragraph is merged correctly. Ragged-right text without paragraph
   spacing gives no signal at such a break; a "last line runs to the edge" rule was tried and
   rejected, because it merged unrelated paragraphs (more churn, measured).

A related bug the long PDF exposed: a wrapped line starting "826. For …" was read as a new list
item. A marker-like start right after a line that runs to the block's edge is now wrapped text
(`test_a_marker_like_start_after_a_full_line_is_wrapped_text`).
