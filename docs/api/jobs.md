# operonx.app.jobs

Jobs run a graph over data that does not talk back — a list, a `.jsonl`
file, a function that yields items — one run per item, every result kept,
with a record per run. `steps=[...]` runs jobs in order. The guide:
[Jobs](../guide/10-jobs.md).

## Job

::: operonx.app.jobs.Job

## The record

::: operonx.app.jobs.JobRun
::: operonx.app.jobs.ItemResult
::: operonx.app.jobs.RunRecord

## Items

::: operonx.app.jobs.iter_items

## The session

::: operonx.app.jobs.JobSession
