# The router: `mode="auto"` — gate

Measured 2026-10-06 with `scripts/bench_router.py` on the K6 work folders (same collections,
embedder `intfloat/multilingual-e5-small` on CPU, default `GraphSpec()`), operonx-kb 0.2.2.
No model call: the router is a rules table (`operonx_kb/retrieval/router.py`, track5 §9.8),
fitted on the K6 **dev** splits only; every number below is the **test** split or a whole
single-hop set. Paired bootstrap (multi-hop) and McNemar (single-hop), 95% intervals.

## Verdict

**`auto` is the default search mode of a collection with a concept graph** (one without
keeps hybrid, or the one index it has). It keeps most of graph search's multi-hop lift and
loses nothing on single-hop questions, where graph search alone cost up to 0.06 Recall@5
(`k6.md`). That is the PLAN rule (a significant lift, no single-hop loss) that graph search
alone did not meet.

| set | routed to graph | R@5 hybrid / graph / **auto** | auto − hybrid, R@5 | auto − hybrid, R@10 |
|---|---|---|---|---|
| 2WikiMultihopQA (300) | 84% | 0.705 / 0.878 / **0.867** | **+0.162** [0.136, 0.187] | **+0.164** [0.137, 0.190] |
| MuSiQue-Ans (300) | 53% | 0.573 / 0.709 / **0.640** | **+0.068** [0.042, 0.092] | **+0.081** [0.055, 0.107] |
| `xquad_en` (397) | 8% | 0.992 / 0.992 / **0.992** | 0.000 [−0.011, 0.011] | 0.000 |
| `xquad_vi` (397) | 0% | 0.995 / 0.977 / **0.995** | 0.000 [−0.011, 0.011] | 0.000 |
| `corpus_vi` (120) | 0% | 1.000 / 0.982 / **1.000** | 0.000 [−0.066, 0.066] | 0.000 |

On 2Wiki `auto` is within noise of graph search (−0.012 R@5, p = 0.08). On MuSiQue it gives
up 0.07 of graph's R@5: 47% of its test questions are worded as single relations ("Who is
the uncle of Liu Bin?", "In which district was Alhandra born?") that no rule tells apart
from a single-hop question. Latency follows the mode: p50 2Wiki 180 → 238 ms, MuSiQue
205 → 300 ms; single-hop sets unchanged.

## The rules

First match wins and names itself in the trace (`search_settings`' `route` output):
role chain ("the mother of the director of …"), role of a work ("the composer of film …"),
possessive role ("Nephalion's father"), place chain ("what county was X born in",
"place of birth"), described entity ("the person after whom …", "the X that …"),
comparison ("born later", "same country", "both").

## Limits

- English only: a Vietnamese relation question goes to the default mode (no Vietnamese
  multi-hop set exists to fit or measure rules on).
- `xquad_en` routes 8% of its single-hop questions to the graph ("the X that …" clauses);
  on that set graph search costs nothing, so the misroutes are free there.
- An LLM classifier (track5 §9.8's optional `LLMOp`) is not built; it would need a
  per-query model call and its own gate.
