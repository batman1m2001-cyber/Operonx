# Plan: one simple `Job`, and guides that follow the installed packages

Status: **draft for review, 2026-10-06.** Nothing is built. Everything using
operonx today is an experiment, so breaking changes are allowed; each one is
listed in §6 with its migration.

Two independent parts, each with its own branch:

- **Part A — Jobs:** one `Job` class (§1–§6).
- **Part B — Guides:** each package ships its guide, and the project copy
  follows what is installed (§7).

§8 reviews this plan as the user would. The design above it already includes
what that review changed.

---

## Part A — Jobs

## 1. Why the current design is the size it is

`operonx.app.jobs` is about 2,700 lines: `Job` (17 parameters), `Runbook` with
`Flow` / `Sequential` / `Parallel` / `>>`, 5 source classes, 7 sink classes,
their YAML configs, the `source:` / `sink:` resource categories, two session
modes, and `[[job]]` in `operonx.toml`.

**Why Runbook exists.** `docs/JOB_PLAN.md` §5 gives one reason: *stage 2 needs
all of stage 1's results first* ("embed everything, then cluster"). Every real
use has that shape:

| Use | What it does | Why it needs Runbook |
|---|---|---|
| callbot `qc` | `qc_cases` writes one file per case (`DirSink`); `qc_report` reads the folder and writes one report | the report needs every verdict |
| ex17 `nightly` | `score_calls` writes `scores.jsonl`; `export_csv` and `summarise` read it | both readers need every score |
| qc-snatcher `main` | `preflight >> ingest >> score >> report`; three of the four run their graph **once, with no items** | the steps are different commands that must run in order, stopping at the first failure |
| qc-snatcher `selfcheck` | `preflight >> ingest_and_seed >> selfcheck_score` (an `Eval`) | the same: a deploy gate as one command |

So Runbook covers **two** needs:

1. **Map-reduce:** collect every item's result, then do one thing with all of
   them. This is also the only reason for `DirSink` and handing data over
   through files. It fits in one `Job` with `reduce=`.
2. **Steps in order:** several jobs as one named command, stopping at the first
   failure. Nothing uses Runbook's parallel branches except ex17. This fits in
   one `Job` with `steps=[...]`, a plain list.

The wiring language (`>>`, lists as parallel branches, `Flow`, `Sequential`,
`Parallel`) is what goes.

**Other findings:**

- **`Job.schedule` does nothing.** It is printed by `--list`; nothing runs it.
- **`session="stream"`** (every item through one run) is used only by ex17. It
  is a Service's shape, and replay (`operonx.app.play`) covers sending
  recorded input through a service.
- **The record does not keep results.** `items.jsonl` holds key, status,
  error, trace id, ms and `sent`, but not the output. The output only went to
  the sink, so the next stage had to re-read the sink.
- **`item_input`** (bind the whole item to one graph parameter) is used by
  callbot QC, callbot backfill, `Eval`, `OnlineEval` and the pytest plugin.
  Binding must keep it.
- **`Eval` / `OnlineEval` / the pytest plugin subclass `Job`.** They pass a
  callable `source`, a callable `sink`, `item_input`, `item_timeout` and
  `on_item`. The new Job has to serve them with no special cases.

## 2. The new `Job`

```python
from operonx.app import Job

qc = Job(
    "qc",
    graph=check_case,          # runs once per item
    items=all_cases,           # what to loop over
    input="case",              # the graph parameter the whole item goes to
    key="id",                  # an item's identity: resume, the record, output
    reduce=score_cases,        # optional: runs once over every result
    concurrency=4,
    on_error="skip",
    preflight=["llm:inhouse"],
)

run = qc.run_sync()            # or: operonx run qc   /   operonx run qc --resume
run.results                    # {key: result}, successful items only
run.failed                     # {key: error}
run.reduced                    # what `reduce` returned (None without one)
```

### Parameters (17 down to 16, but each one simpler)

| Parameter | Meaning |
|---|---|
| `name` | The job's name: the CLI argument and the record folder. |
| `graph` | A module-level `@graph` or a compiled `Operon`. |
| `items` | What to loop over (§2.1). |
| `input` | Optional. The graph parameter that receives the whole item (§2.2). |
| `inputs` | Fixed graph inputs for every item. |
| `key` | Item field name, or a function of the item. Without it, there is no resume. |
| `output` | Optional export: a `.jsonl` path or a function (§2.3). |
| `reduce` | Optional `@graph` run once over every result (§2.4). |
| `concurrency` | Items in flight (default 4). |
| `on_error` | `"skip"` (default) or `"stop"`. |
| `retry`, `timeout` | Per item: `Retry(...)` and seconds. The same types `@op` uses. |
| `preflight` | Resource keys that must answer before any item runs. |
| `steps` | Instead of `graph`: other jobs to run in order (§2.4b). |
| `trace` | Trace consumers, below `[tracing]` in precedence, as today. qc-snatcher picks its tracer from the environment here. |
| `description` | One line, for `--list` and the studio. |

**Removed:** `source`, `sink`, `session`, `max_inflight`, `item_input`
(renamed `input`), `item_timeout` (now `timeout`), `on_item` (moved to
`run(on_item=)`), `record_dir` (a project setting, §2.5), `schedule` (§2.6).

### 2.1 `items`: something you can loop over, or a `.jsonl` file

- **A function** returning an iterable or async iterable, including a
  generator function. It is **called on every run**, so it re-reads its data
  each time. This is the custom loader:
  ```python
  def todays_calls():
      for row in db.query("select id, audio from calls where day = today()"):
          yield {"call_id": row.id, "audio": row.audio}
  ```
- **An iterable or async iterable:** a list, or any object with `__iter__` /
  `__aiter__`. You subclass nothing from operonx.
- **A path to a `.jsonl` file:** one item per line. It's the one convenience,
  because it's the common case and works from the command line.

**Not accepted, on purpose:**

- **No eval `Dataset`.** A dataset row is a test case (`input`, `expected`,
  `split`, `status`). Feeding it to a job would need eval rules inside Job.
  The explicit form is one line:
  `items=lambda: (c["input"] for c in Dataset("cases.jsonl").select(split="test").rows())`.
- **No folders, globs or CSV.** `lambda: Path("logs").glob("*/*/call_*.jsonl")`
  and `csv.DictReader` are plain Python.

### 2.2 Binding an item to the graph

1. **With `input="case"`:** the whole item goes to the `case` parameter.
   This is today's `item_input`.
2. **Without `input`, and the item is a dict:** its keys fill parameters by
   name. A key the graph does not take is an error that names it, so a typo
   cannot pass silently.
3. **Without `input`, and the item is not a dict:** it goes to the graph's only
   free parameter (one not set by `inputs=`). If there are several, the error
   says to pass `input=`.
4. **A graph with doors** (`ingress` / `egress`): the item enters through
   `ingress`, and the result is what `egress` sent. This keeps "the same graph
   as a Service and as a Job".

### 2.3 Results and `output`

- **The result** of a graph without doors is its outputs, without the `$`
  keys. For a graph with doors, it's the item `egress` sent, or a list if it
  sent several. An item that sends or returns nothing has status `empty`, as
  today.
- **Every result is written to the record** in `results.jsonl`, next to
  `items.jsonl` (`{"key", "result"}` per line), as each item finishes. This
  makes `run.results`, `--resume` and `reduce` work together: a resumed run
  loads the results earlier runs kept.
- **`output=` is only an export,** of successful items:
  - `output="out/scores.jsonl"` appends `{"key", "result"}` lines as items
    finish;
  - `output=save` calls `save(key, result)`, sync or async, as items finish
    (a database write, an upload).
- **Failures go only to the record,** with their error and trace id.
- **`keep_results=False`** turns off `results.jsonl` for outputs too large to
  keep. Then `reduce` and `run.results` are unavailable, and the Job refuses
  `reduce` at construction.

### 2.4 `reduce`

A module-level `@graph` with a `results` parameter, plus anything in `inputs=`.
It runs once, after the last item, on a list of results in key order. It
includes results kept by earlier runs when resuming, and only successful items.
Its outputs are `run.reduced`, written to `run.json`, and its trace is one more
run tagged with the job.

```python
@graph
def score_cases(results):
    r = report(verdicts=results)
    START >> r >> END
```

Under `on_error="stop"`, a failed item means `reduce` does not run. Under
`"skip"`, it runs on what succeeded. `run.failed` says what is missing.

### 2.4b `steps`: jobs in order, as one command

```python
main = Job("main", steps=[preflight, ingest, score, report],
           description="The main flow: preflight, ingest, score, report.")
```

- **Order:** the steps run one after another. The first step whose run is not
  `ok` stops the job, and the rest are recorded `skipped`.
- **`--resume`** passes through to every step, so a step that finished is
  skipped item by item, as it would be on its own.
- **The record:** each step keeps its own record. The `steps` job's `run.json`
  lists each step's status and run id, which is what Runbook's `run.json` held.
- **Exclusive:** a `steps` job takes only `name`, `steps` and `description`.
  Passing `graph`, `items` or `reduce` with it is an error.
- **No parallel branches, conditions or nesting rules.** A step may itself be
  a `steps` job. Anything more (a condition, a loop) is a Python function
  calling `job.run()`.

### 2.4c No `items`: run the graph once

A Job without `items` runs its graph once with `inputs` (and `--set`), as
today. Three of qc-snatcher's four `main` steps are this shape (preflight,
ingest, report).

### 2.5 The record

```
.operonx/jobs/<job>/<run_id>/
  run.json        status, counts, timing, the reduce result, the job's settings
  items.jsonl     key, status, error, trace_id, ms, attempts   (as today)
  results.jsonl   key, result                                  (new)
```

The folder is `.operonx/jobs` under the project root, or `[jobs] dir` in
`operonx.toml`. `Job(..., record_dir=)` is gone; `run(record_dir=)` stays for
tests.

### 2.6 Schedules

`schedule=` is removed. It never ran, and the standard way to run a command on
a clock is cron, a systemd timer, or the CI scheduler running `operonx run qc`.
The CLI exits non-zero when the run fails, which is what those tools need. If a
built-in clock is wanted later, the Service `schedule()` listener can start a
job: one place, built once. It is not in this plan.

### 2.7 CLI

```
operonx run qc                 # run it (with reduce, if any)
operonx run qc --resume        # only keys not ok yet; reduce sees all results
operonx run qc --set k=v       # an input
operonx run --list             # the Application's jobs
```

`--only` and the Runbook flags go. Exit code: 0 if the run is `ok`, 1 if any
item failed, 2 if the job could not start (preflight, bad binding).

## 3. What is removed

| Removed | Replaced by |
|---|---|
| `Runbook`, `Flow`, `Sequential`, `Parallel`, `RunbookRun`, `NodeReport` | `reduce=` (map-reduce) or `steps=[...]` (jobs in order) |
| `JsonlSource`, `CsvSource`, `PythonSource`, `DirSource`, `Source`, `SourceConfig`, `as_source`, `create_source`, `open_source` | `items=` (§2.1) |
| `JsonlSink`, `CsvSink`, `ListSink`, `PythonSink`, `DirSink`, `NullSink`, `Sink`, `SinkConfig`, `as_sink`, `create_sink`, `open_sink` | the record, plus `output=` |
| `source:` / `sink:` resource categories | a path, or a function that reads the resource |
| `session="stream"`, `max_inflight`, `run_stream`, `JobSession` (stream half) | a Service, or replay |
| `"retry:N"` and `"record"` in `on_error` | `retry=Retry(N)`; failures are always in the record |
| `[[job]]` in `operonx.toml` (and `runbook =`) | `Job(...)` in `app/main.py`, listed in `Application(jobs=[...])` |
| `Job(schedule=)` | cron / CI running `operonx run` |

Estimate: the jobs package drops from ~2,700 to ~1,100 lines, plus manifest and
declare code.

## 4. What stays and is untouched

- **Per-item runs:** each item is its own Operon run with its own trace,
  tagged `job`, `job_run` and `key`.
- **The record and resume semantics:** `ok` / `failed` / `empty` / `skipped` /
  `timeout`, written as items finish.
- **`preflight`, `concurrency`, keys.**
- **`Application(jobs=[...])`, `operonx run --list`,** and the studio's run
  list, which reads records rather than Runbook objects.
- **`Eval`, `OnlineEval` and the pytest plugin** keep their public API. Inside,
  they pass `items=`, `input=` and `output=` instead of
  `source=` / `item_input=` / `sink=`.

## 5. Phases

| Phase | Work | Gate |
|---|---|---|
| A1 | New `Job` and runner: binding (§2.2), `results.jsonl`, `output`, `reduce`, `retry` / `timeout`, exit codes; remove §3 | jobs tests rewritten and green; resume keeps results (killed run → `--resume` → `reduce` sees every key); binding errors name the field |
| A2 | `Eval`, `OnlineEval`, pytest plugin, `operonx run`, `Application`, declare, manifest | the whole core suite green; eval reports unchanged on the existing eval fixtures |
| A3 | Docs: guide 02 and 05, `docs/guide/10-jobs.md`, ex17 rewritten (QC-style map + reduce), `init` templates, MIGRATION.md, CHANGELOG | guide snippets run (`tests/guide`); `operonx init` → `operonx run` works in a fresh folder |
| A4 | Consumers (§6.1): operonx-kb `S3Source` / `DriveSource` become `__aiter__` (kb 0.2.4); callbot QC + backfill (`refactor/operonx-studio` only); qc-snatcher `main` / `selfcheck` become `steps=`; meeting-prep's zone test; studio shows `steps` where it showed a runbook | each repo's suite green; callbot `operonx run qc` on a 3-case fixture, as `tests/qc/test_qc.py` does; qc-snatcher `operonx run selfcheck` |

Release: operonx **1.17.0** (breaking), then kb 0.2.4.

## 6. Migration examples

### 6.1 Who is affected (audited 2026-10-06)

| Project | Uses | Change |
|---|---|---|
| educa-reminder-agent, `staging` | nothing from jobs | none |
| callbot `refactor/operonx-studio` | `Job`, `Runbook` (qc), `DirSink`, `DirSource`, `source:call_logs` | QC becomes one `reduce=` job; backfill takes `items=`. Only on this branch. |
| qc-snatcher | 2 Runbooks, ~8 Jobs with `item_input`, `trace=` | `Runbook(...)` becomes `Job(..., steps=[...])`; `item_input` becomes `input` |
| meeting-prep-operonx | one test: `Job(source=[item], sink=[])` | becomes `Job(items=[item])` |
| operonx-kb | `S3Source` / `DriveSource` (`items()` method) | rename to `__aiter__` |
| studio | the runbook view | shows `steps` |

```python
# qc-snatcher: before
with Runbook("main", record_dir=RUNS, description="...") as main:
    preflight >> ingest >> score >> report
# after
main = Job("main", steps=[preflight, ingest, score, report], description="...")
```

```python
# before: two jobs, a DirSink, a Runbook
qc_cases  = Job("qc_cases", graph=check_case, source=all_cases, item_input="case", key="id",
                sink=DirSink(OUT / "qc"), on_error="record", record_dir=OUT / "jobs")
qc_report = Job("qc_report", graph=score_cases, inputs={"qc_dir": str(OUT / "qc")},
                sink=OUT / "qc_report.jsonl", record_dir=OUT / "jobs")
qc = Runbook("qc", qc_cases >> qc_report, schedule="0 3 * * *", record_dir=OUT / "jobs")

# after: one job; score_cases takes `results` instead of reading a folder
qc = Job("qc", graph=check_case, items=all_cases, input="case", key="id",
         reduce=score_cases, preflight=["llm:inhouse"])
```

```python
# before
Job("backfill_call_logs", graph=backfill_log, source="source:call_logs", item_input="file",
    key="name", inputs={"dry_run": False}, sink=OUT / "backfill.jsonl", concurrency=8)
# after: the graph's `file` parameter now gets a Path, not {"path", "name"}
Job("backfill_call_logs", graph=backfill_log, input="file", key=lambda p: p.name,
    items=lambda: Path(LOG_DIR).glob("*/*/call_*.jsonl"), inputs={"dry_run": False},
    concurrency=8)
```

---

## Part B — Guides that follow the installed packages

## 7. Design

**Today:** the core package ships `operonx/guide/` (pages 01–09). Page
`09-agents.md` documents operonx-agents, a separately released package. KB has
no guide. `operonx init` and `operonx guide --sync` copy the core pages to
`.operonx/guide/`. `AGENTS.md` lists the page names. Nothing notices
`uv add operonx-agents` or an upgrade.

**After:**

1. **Each package ships and tests its own guide:**
   - `operonx/guide/` (core);
   - `operonx_agents/guide/`: `09-agents.md` moves here, split if needed;
   - `operonx_kb/guide/`: new, covering the library, flows, `kb_tools`, MCP.

   Each package's test suite runs its own snippets, as `tests/guide` does for
   core.
2. **Discovery uses an entry point,** the mechanism `operonx.resources`
   already uses:
   ```toml
   [project.entry-points."operonx.guides"]
   agents = "operonx_agents.guide"
   ```
   Core registers `core`.
3. **The project copy mirrors what's installed:**
   ```
   .operonx/guide/README.md     generated: each installed package, its version, its pages
   .operonx/guide/core/…
   .operonx/guide/agents/…
   .operonx/guide/kb/…
   ```
   A sync adds a new package, rewrites a changed version, and deletes an
   uninstalled package's folder. Running it twice changes nothing.
4. **When it syncs:**
   - **`operonx init`.**
   - **`operonx guide`** with no flags (sync and print the index path).
     `--path` and `--check` stay; `--check` exits 1 when the copy is stale,
     for CI.
   - **Any `operonx` command inside a project** (`run`, `serve`, `eval`)
     compares the installed versions with the copy's `README.md` header. If
     they differ, it syncs and prints one line: `guide: +agents 0.1.3,
     core 1.16.0 → 1.17.0`. It's a generated folder, never hand-edited, so
     rewriting it is safe.
5. **`AGENTS.md` stays the user's file.** Only a marked block is regenerated:
   ```
   <!-- operonx:guide -->
   Installed: operonx 1.17.0, operonx-agents 0.1.3. Read .operonx/guide/README.md first.
   After `uv add` / `uv sync` of an operonx package, run `operonx guide`.
   <!-- /operonx:guide -->
   ```
   A project from before this change gets the block appended once. The
   hard-coded page list is removed from the template.

The user's flow:

```bash
uv add operonx && uv run operonx init          # core guide
uv add operonx-agents operonx-kb               # then any operonx command, or:
uv run operonx guide                           # → core + agents + kb, one index
```

Phase **B1**: entry points and sync in core, the AGENTS.md block, init
template. Phase **B2**: move page 09 to agents, write the KB page, add each
package's snippet tests. Gate: in a fresh folder, `init` → `uv add
operonx-agents` → `operonx run --list` leaves `.operonx/guide/agents/`;
`uv remove` → `operonx guide` removes it; `--check` exits 1 when stale.
Releases: operonx 1.17.x, agents 0.1.4, kb 0.2.4.

---

## 8. Review as the user

Questions a user would ask, and where the plan ended up.

1. **"Why keep `key` at all — can't it default?"** It could default to an
   `id` field, but a guessed identity silently breaks resume when the guess is
   wrong. **Kept explicit.** Without `key`, items get random ids, and
   `--resume` refuses with a message saying so.
2. **"Dicts fill parameters by name — then how does QC pass the whole case?"**
   The first draft had only the by-name rule. The audit found five users that
   bind the whole item. **Added `input=`** (§2.2). It's the old `item_input`
   under a shorter name.
3. **"`reduce` loads every result into memory. What about a million
   embeddings?"** True. For that size, `reduce` should read `results.jsonl`
   itself, or not be used. **Kept as a list:** it is the simple case that
   works for every current user. `keep_results=False` refuses `reduce`, so the
   failure is at construction, not at the end of a long run.
4. **"Why reimplement cron?"** The draft kept `schedule=` and promised to make
   it run. **Removed** (§2.6): cron, systemd and CI already do it, and
   `operonx run` exits non-zero on failure.
5. **"Is `output=` needed if the record keeps results?"** For a job whose
   whole point is writing somewhere (backfill, export), yes: a database write
   per item, as it finishes, needs a hook. Kept to a path or a function, with
   no classes.
6. **"Is `on_error='skip'` enough without `'record'`?"** `record` existed so a
   failure could reach a sink as data. Failures are now always in the record,
   with their error. **Two values remain.**
7. **"A `.jsonl` path is a special case too — why keep it?"** It's the common
   case, and the only form that works from the command line or a config.
   Everything else is Python. **Kept, and nothing else.**
8. **"Do doors still matter for jobs?"** Yes: "the same graph as a Service
   and a Job" is the reason `ingress` / `egress` resolve from a session. ex17
   and the KB ingest graph use it. **Kept.** The result is defined in §2.3.
9. **"Auto-syncing the guide on every command writes files I didn't ask
   for."** Only `.operonx/guide/`, which is generated. One line says what
   changed. The alternative — a stale guide an assistant trusts — is worse.
   **Kept,** with `--check` for CI.
10. **"Templates contributed by packages?"** Out of scope. `init --template
    agent` keeps its `requires=` line. Revisit only if a second package needs
    a template.

11. **"Removing Runbook breaks qc-snatcher's `main` and `selfcheck`."** The
    first draft only saw the map-reduce users. qc-snatcher chains different
    one-shot jobs as one command, a second real need. **Added `steps=[...]`,**
    a plain ordered list, with no operator, no parallel branches and no
    second wiring language.
12. **"Why drop `trace=` when qc-snatcher picks its tracer from the
    environment?"** It costs one parameter, and the precedence logic already
    exists. **Kept.**
## 9. Open (your call)

- Leave **TOML `[[serve]]`** in this plan, or remove it in the same release as
  `[[job]]`? (Recommended: a separate change; services are not part of this
  plan.)
