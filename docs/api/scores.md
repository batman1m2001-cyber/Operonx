# operonx.telemetry.scores

Where experiments, their items and every score live: an eval's checks, a
judge's verdicts, a human's review, an online rule's results — one score
row with a target and an id derived from what it judges. Backends are the
run stores': files (the default), SQLite, and ClickHouse (schema version
3, in the runs' database). The guide: [Evals](../guide/13-evals.md#experiments-in-a-score-store).

::: operonx.telemetry.scores
    options:
      members: false

## The contract

::: operonx.telemetry.scores.ScoreStore

## The data

::: operonx.telemetry.scores.Experiment
::: operonx.telemetry.scores.ExperimentItem
::: operonx.telemetry.scores.Score
::: operonx.telemetry.scores.score_id_of
::: operonx.telemetry.scores.ExperimentRecord
::: operonx.telemetry.scores.ExperimentPage
::: operonx.telemetry.scores.ExperimentFilter
::: operonx.telemetry.scores.ScoreFilter
::: operonx.telemetry.scores.Bucket

## Configuration

::: operonx.telemetry.scores.ScoreStoreConfig
::: operonx.telemetry.scores.open_score_store

## Backends

::: operonx.telemetry.scores.files.FilesScoreStore
::: operonx.telemetry.scores.sqlite.SqliteScoreStore
::: operonx.telemetry.scores.clickhouse.ClickHouseScoreStore
