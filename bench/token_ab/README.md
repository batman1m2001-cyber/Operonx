# Token A/B: does the operonx project environment pay for itself?

ENGINEERING_ENV_PLAN E7 (board C30). Both arms get the same product spec ([SPEC.md](SPEC.md)) and the
same seven tasks ([tasks.py](tasks.py)), each run as a fresh Claude session (Sonnet, headless):

| arm | starts from |
|---|---|
| A | `operonx init support --web --http --editable <Operon>`: the guide, AGENTS.md (it also had knowledge.md and `operonx-project` on PATH until operonx 1.47.0) |
| B | an empty folder |

After each task, a hidden black-box check ([accept.py](accept.py)) starts `./run.sh` with a fresh
`$DATA_DIR`, calls the HTTP routes and jobs, and renders the pages in headless Chromium. A failed check
gets one fix turn (`--resume`), which is counted. The product's model is [fake_llm.py](fake_llm.py),
which costs nothing: it classifies by keywords, answers a summary with `SUMMARY: …`, and can answer 503
once per request (T5a/T5b).

```
python run.py OUT --tracks A1,A2,B1,B2 --cap 40     # OUT/results.jsonl, OUT/summary.json, OUT/<track>-<task>.jsonl
```

**Gate 2 (COST_PLAN E7b)** adds three tasks, T7–T9 (`E7B` in [tasks.py](tasks.py)): trace a slow
request (`GET /timings`), stream a summary (Server-Sent Events from a `"stream": true` call), and a
scheduled job plus a resumable one. They run after T6, each track in the workspace an earlier run left:

```
python run.py OUT --tasks T7,T8,T9 --tracks A1,A2,B1,B2 --ws A1=RUN/ws/A1/support,...,B2=RUN/ws/B2/support --cap 8
```

The fake model can wait (`delay_ms`), streams one word per chunk, and can fail every summary call
after the first N (`fail_summary_after`). A stdlib reference solution passed T7–T9 (and T1–T6 still
pass) before the run, so a failure is the session's, not the check's.

It counts, per session, `total_cost_usd`, the tokens used, the number of turns, and the doc tokens read
(Read, Bash or Grep results on `.operonx/`, AGENTS.md, CLAUDE.md, README.md, `docs/brd/` or
`operonx-project`, at chars / 4). The run stops before a session that could take the spend past `--cap`.

The rows of the 2026-11-15 run are in [results/](results/); the analysis is in
[docs/engineering_env/E7.md](../../docs/engineering_env/E7.md).
