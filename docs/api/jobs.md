# operonx.core.jobs

Jobs run an Operon over data that does not talk back — a file, a table,
an iterable — one run per item, with a record per run. Runbooks order
jobs. The guide: [Jobs and Runbooks](../guide/10-jobs.md).

## Job

::: operonx.core.jobs.Job

## Runbook

::: operonx.core.jobs.Runbook
::: operonx.core.jobs.Sequential
::: operonx.core.jobs.Parallel

## The record

::: operonx.core.jobs.JobRun
::: operonx.core.jobs.ItemResult
::: operonx.core.jobs.RunbookRun
::: operonx.core.jobs.NodeReport

## Sources and sinks

::: operonx.core.jobs.Source
::: operonx.core.jobs.JsonlSource
::: operonx.core.jobs.CsvSource
::: operonx.core.jobs.PythonSource
::: operonx.core.jobs.Sink
::: operonx.core.jobs.JsonlSink
::: operonx.core.jobs.CsvSink
::: operonx.core.jobs.ListSink
::: operonx.core.jobs.PythonSink
::: operonx.core.jobs.NullSink

## The session

::: operonx.core.jobs.JobSession
