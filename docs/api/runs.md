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

## ClickHouse

::: operonx.telemetry.runs.clickhouse.ClickHouseRunStore
::: operonx.telemetry.consumers.clickhouse.ClickHouseConsumerConfig

## Media

::: operonx.telemetry.media
    options:
      members: false
::: operonx.telemetry.media.detect_media
::: operonx.telemetry.media.MediaInfo
::: operonx.telemetry.media.MediaStore
::: operonx.telemetry.media.LocalMediaStore
::: operonx.telemetry.media.offload_to_store
::: operonx.telemetry.media.json_default

## The background writer

::: operonx.telemetry.writer.BackgroundWriter

## Retention

::: operonx.telemetry.runs.retention

## Alerts

::: operonx.telemetry.runs.alerts
