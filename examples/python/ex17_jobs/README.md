# 17 · Jobs — one graph, run over a file

```
                         ┌───────────────────────────┐
 data/calls.jsonl ─[Job]─►  ingress ─► score ─► egress ├─► scores.jsonl
                         └───────────────────────────┘
                                      ▲
                         the same graph `[[serve]]` would put
                         behind an HTTP route, unchanged
```

A `Job` is what `[[serve]]` is for a listener: it names a graph, where the
items come from, where results go, what identifies an item, and what a
failure means. One run per item; the graph is not told it is in a batch.

## What it buys over a for-loop

Every run leaves a record:

```
/tmp/operonx_jobs/ex17/jobs/score_calls/<run_id>/
  run.json      status, counts {ok, failed, empty, skipped}, what ran
  items.jsonl   {key, status, error, trace_id, ms, sent} — one line per call
```

- **failed** items name the op and the error.
- **empty** items ran cleanly and sent nothing — the batch bug that
  otherwise reports OK.
- `--resume` reads the last run and touches only the keys that are not done.

## Run it

```bash
uv sync
uv run python main.py
#   score_calls 20260924T… failed  ok=2 failed=1 empty=0 skipped=0
#   failed c3: scored: ValueError: empty transcript
```

Give call `c3` a transcript in `data/calls.jsonl`, then:

```bash
uv run operonx-run main:score_calls --resume
#   score_calls 20260924T… ok  ok=1 failed=0 empty=0 skipped=2
uv run operonx-run main:score_calls --show      # what would run, and exit
```

`score_from_resources` is the same job with its source and sink declared
in `resources.yaml` — where a deployment names them, so the job stays
literal:

```yaml
source:calls: {kind: jsonl, path: data/calls.jsonl}
sink:scores:  {kind: jsonl, path: /tmp/operonx_jobs/ex17/scores_from_resources.jsonl}
```

```bash
uv run operonx-run main:score_from_resources
```

## The manifest form

`operonx.toml` declares the same jobs as `[[job]]` blocks — the
deployment's form, next to the `[[serve]]` block that puts the *same*
graph behind an HTTP route:

```bash
uv run operonx-run --list                     # every [[job]], with its schedule
uv run operonx-run score_calls                # by name; paths relative to the manifest
uv run operonx-run score_calls --resume
uv run operonx-run score_stream               # session = "stream": one run, all calls
uv run operonx-serve --only score             # the served form of the same graph
```

A stream job records what was fed and what egress sent, plus the one
trace id; it cannot resume, because one run has no per-item outcomes.

Every run's traces carry `job`, `job_run` and `key`, as fields and as
tags, so a Langfuse filter finds one job, one run, or one item.

Design and the phase still to come (the Runbook: many jobs, one
command): `docs/JOB_PLAN.md` in the operonx repo.
