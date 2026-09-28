# operonx.telemetry.runs

Where finished runs live, and how they are asked for. A store keeps each
run in full and as a summary with per-op rollups; it is a trace consumer
and a resource (`run_store:`). The guide: [Runs: stores, retention and
alerts](../guide/11-runs.md).

::: operonx.telemetry.runs
    options:
      members: false

## The contract

::: operonx.telemetry.runs.RunStore

## The data

::: operonx.telemetry.runs.RunFilter
::: operonx.telemetry.runs.RunSummary
::: operonx.telemetry.runs.RunRecord
::: operonx.telemetry.runs.OpRollup
::: operonx.telemetry.runs.OpStats
::: operonx.telemetry.runs.Page
::: operonx.telemetry.runs.summarize
::: operonx.telemetry.runs.percentile

## Configuration

::: operonx.telemetry.runs.RunStoreConfig
::: operonx.telemetry.runs.open_run_store

## Retention

::: operonx.telemetry.runs.retention

## Alerts

::: operonx.telemetry.runs.alerts
