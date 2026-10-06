# K1 gate (e): PDF ingest throughput

Measured 2026-10-04 with `scripts/bench_ingest.py --pages 60 --repeat 3` (medians of 3), Intel Xeon
E5-2686 v4 @ 2.30GHz, 24 vCPU, Python 3.11, docling-parse 7.22.1, operonx `feat/kb-upstream`
(cd8f155). Ingest uses the HashEmbedder and an in-memory FAISS index, so no model latency is included;
the engine is built by a warm-up document before timing. No target was set for K1: these are the
baseline numbers.

| file | pages | parse median (s) | backend (s) | layout (s) | parse pages/s | ingest median (s) | ingest pages/s |
|---|---|---|---|---|---|---|---|
| chinh_sach_vi.pdf | 1 | 0.013 | 0.011 | 0.002 | 75.4 | 0.151 | 6.6 |
| table_report.pdf | 1 | 0.027 | 0.022 | 0.005 | 37.4 | 0.125 | 8.0 |
| two_column_report.pdf | 2 | 0.078 | 0.066 | 0.012 | 25.6 | 0.264 | 7.6 |
| generated_60p.pdf | 61 | 4.704 | 4.020 | 0.659 | 13.0 | 5.774 | 10.6 |

## Where the time goes

- **Parsing is the docling-parse backend**: 4.0 of 4.7 s on the 61-page document (86%, ~66 ms/page).
  Our heuristic layout is 0.66 s (~11 ms/page). No native hotspot in our code justifies Rust (PLAN D5).
- **Per-document fixed cost ~0.12 s** (small documents ingest at 0.13 s while parsing takes 0.013 s).
  Measured: a catalog write transaction costs ~14 ms (`sqlite3` commit fsync plus the WAL checkpoint
  when the per-transaction connection closes; reads ~1.8 ms), and an ingest runs 5 of them (commit,
  ingest log, index ledger, embedding cache, ledger forget). `PRAGMA synchronous=NORMAL` was tried
  and did not help (13.8 ms), because the close-time checkpoint grows to match; it was reverted.
  Reusing a connection per thread is the candidate fix, to be measured before it is made.
- Chunking, structure and commit add ~1.1 s on 61 pages over parsing alone.
