# operonx.app.jobs

Jobs run an Operon over data that does not talk back — a file, a table,
an iterable — one run per item, with a record per run. Runbooks order
jobs. The guide: [Jobs and Runbooks](../guide/10-jobs.md).

## Job

::: operonx.app.jobs.Job

## Runbook

::: operonx.app.jobs.Runbook
::: operonx.app.jobs.Sequential
::: operonx.app.jobs.Parallel

## The record

::: operonx.app.jobs.JobRun
::: operonx.app.jobs.ItemResult
::: operonx.app.jobs.RunbookRun
::: operonx.app.jobs.NodeReport

## Sources and sinks

::: operonx.app.jobs.Source
::: operonx.app.jobs.JsonlSource
::: operonx.app.jobs.CsvSource
::: operonx.app.jobs.PythonSource
::: operonx.app.jobs.Sink
::: operonx.app.jobs.JsonlSink
::: operonx.app.jobs.CsvSink
::: operonx.app.jobs.ListSink
::: operonx.app.jobs.PythonSink
::: operonx.app.jobs.NullSink

## The session

::: operonx.app.jobs.JobSession
