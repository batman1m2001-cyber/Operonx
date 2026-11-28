# Run environment plan: every run says which environment made it

Status: **PLAN, not started** (2026-11-28). Repositories: Operon (E1), operonx-studio (E2), callbot (E3).

## 0. The rule

One trace database per project, as today. A run carries the name of the environment that made
it, set once on the service:

```bash
OPERONX_ENVIRONMENT=production
```

The studio filters by it. A run made without it has an empty environment, shown as "not set".

This is what Langfuse does since 2025-03 (`LANGFUSE_TRACING_ENVIRONMENT`, an `environment` on
every trace, a filter in the UI) and what OpenTelemetry names `deployment.environment.name`.
Before that, Langfuse's advice was a project per environment; they replaced it.

## 1. Audit (2026-11-28, Operon `d7c50b52`, studio `59d6d39`, callbot `3ed6a6e`)

| Fact | Where |
|---|---|
| callbot production, staging and dev write to one database, `callbot_traces`, with one account | `.env_prod`, `.env_staging`, `.env_dev`: the five `CH_TRACES_*` are equal |
| The run tables have no environment column | `operonx/telemetry/runs/clickhouse.py` `_DDL_RUNS` |
| Every run already carries what the process was stamped with: the project and the code's version | `operonx/app/origin.py` `stamp_process` → `set_run_metadata` |
| `version` is a column, a field of `RunFilter`, and is read by every backend | `sql.py` `SUMMARY_COLUMNS`, `model.py` `RunFilter`, `clickhouse.py`, `mongo.py`, `files.py` |
| The version is read from git. The images have no git, so container runs have none | 1,896 runs since 2026-11-27 with `version` NULL |
| The schema changes by numbered migrations, applied when a store opens | `clickhouse.py` `MIGRATIONS` (5 so far) |
| The studio's Runs page filters by time, status and order; Monitor by service and time | `static/runs.js`, `monitor.py` |
| The callbot's tests write runs to the shared database | 6 runs per `pytest`, `version` = the checkout's commit |

In `callbot_traces` today: 103 runs before production moved to operonx (2026-11-26 16:34 UTC),
all with a development commit as version; 1,896 after, from containers, production and staging
mixed.

Why a column and not a key in `metadata`: `RunFilter.metadata` would filter the Runs list with
no schema change, but Monitor reads `op_rollups`, which has no metadata. A column, as for
`version`, serves both.

## 2. After

```text
OPERONX_ENVIRONMENT=staging        on the service (env file, compose, shell)
        │
stamp_process()                    once per process: project, version, environment
        │
runs.environment                   LowCardinality(String), '' when not set
op_rollups.environment             the same, for Monitor
        │
RunFilter(environment="staging")   every backend
        │
studio: Runs and Monitor           an environment picker beside the status one
```

A name is 1–32 of `a-z 0-9 _ -`, lowercased; anything else is refused at start with a message,
not written. operonx fixes no list: `production`, `staging`, `dev`, `test` are a convention.

## 3. Phases

Each phase ships alone and leaves what runs working.

| | What | Done when |
|---|---|---|
| **E1** operonx | `OPERONX_ENVIRONMENT` read in `stamp_process`; `environment` on `RunSummary`, in `SUMMARY_COLUMNS`, `RunFilter`, and every run store (ClickHouse migration 6: `ADD COLUMN` on `runs` and `op_rollups`; SQL stores; files; Mongo); a way to list the environments a store holds. A minor version. | Two processes with different names write to one database, and a filtered list returns each one's runs only. Rows written before read as `''`. A writer on the previous operonx still writes to the migrated database. |
| **E2** studio | A picker "Any environment / production / staging / …" on Runs and on Monitor, always present, filled from what the store holds; the choice is kept per project. | On a store with two environments, each screen shows one, both, and the unset ones. Screens at 1280, 800 and 400 px. |
| **E3** callbot | `OPERONX_ENVIRONMENT` in the four env files (`production`, `staging`, `dev`, `test`); operonx bumped; the 103 early runs labelled `dev`; the tests stop writing to the shared database. | After a staging deploy, new staging runs carry `staging`, and production's after its deploy. |

Order of deploy: staging first. Until production is deployed its runs stay unset, and that is
visible, not wrong.

## 4. Not in this plan

- A database per environment. Three were created on 2026-11-28 (`callbot_traces_prod`,
  `_staging`, `_dev`) and are empty: drop them.
- Rights or retention per environment. One account, one retention, as today.
- Labelling the 1,896 mixed container runs. Only if another field tells them apart; not looked at.
- Environment on scores and experiments. They join to runs by trace id.
- Importing the old production traces from Langfuse. The next plan; it lands on top of this one,
  as `environment = production`.

## 5. Risks

| Risk | Answer |
|---|---|
| A migration on a database production writes to | `ADD COLUMN` with a default is a metadata change in ClickHouse: no rewrite, no lock on inserts. E1 tests an old writer against the new schema. |
| Staging is upgraded and migrates the shared database while production runs the old operonx | The old writer names its columns, so the new one takes its default. Tested in E1, and it is the order of deploy on purpose. |
| A typo makes a third environment (`prodution`) | The picker shows every name the store holds, so it is seen at once. Names are not renamed in place; the service is fixed and the stray runs stay under their name. |
| The variable is forgotten on a service | Its runs are "not set", a named choice in the picker. |
