# Track 4: Evaluation system for OperonX workflows, integrated with OperonX Studio

Status: design proposal, 2026-10-04. No repository was modified.
Code baselines: OperonX `a21082d` (v1.14.0); operonx-studio `7de6d60`.
Line references are `path:line` against those commits.

---

## 0. TL;DR: the decisions

1. **Build on what exists.** OperonX has had evals since 1.9.0: `Eval` is a `Job`
   with `origin=eval` (`operonx/app/evals.py:342`). Studio already has Evals,
   Datasets and Review screens. The design extends these pieces and does
   not add a parallel system. An eval run *is* an **experiment**. An
   experiment item *is* a job item. A score *is* a verdict that has moved into a store.
2. **Datasets stay JSONL files in git**, which is the source of truth and is reviewable in
   merge requests. Each dataset is versioned by a content hash. The database never holds the only copy.
3. **Scores, experiments and experiment items go to a new `ScoreStore`.** It
   runs on the same backends as the run stores: files+SQLite locally and ClickHouse for teams and CI.
   Today verdicts live only in local `items.jsonl` (`operonx/app/jobs/record.py:85`),
   so experiments produced in CI never reach Studio.
4. **Evaluators can see the trace.** A new `trace` argument passes a `TraceView`
   over the per-op rows (inputs/outputs/status/cost). The same view
   is built from a live trace (offline eval) or from a stored `RunRecord`
   (online eval or rescoring). Trajectory, tool-call and budget evaluators sit on top of it.
5. **LLM judges are operonx graphs and are traced.** Each judge call becomes its own
   trace (`origin=eval`, `role=judge`). Judge results are versioned (rubric + model
   + graph hash), cached, and binary by default. Pairwise judging uses a position
   swap. A judge has to show agreement with humans (Cohen's κ, TPR/TNR) before
   it can gate quietly.
6. **Gating is statistical and has three states:** `pass` / `regressed` /
   `inconclusive`. The comparison is paired: paired bootstrap CI on per-case differences, plus an exact McNemar test for
   binary checks. Clustered SE is used when cases share a scenario. `repeats=N` handles flakiness
   (pass^k). "Must-pass" cases are a separate, deterministic tier.
7. **Online eval is a scheduled Job over the run store**: deterministic
   hash sampling, a budget, idempotent score ids. It never runs inline in the
   service hot path, because callbot latency is sacred. Failures feed review queues, and
   score metrics feed the existing alerts.
8. **Surfaces:** `operonx eval …` CLI (run/compare/report/rescore/calibrate/
   power/align/online), an opt-in pytest plugin, JUnit + Markdown reports
   for CI, and Studio pages for experiments, compare, case drill-down to trace,
   datasets editor, online scores, queues and judge alignment.
9. **Placement:** everything except UI stays in core `operonx`, with no new dependencies
   (stats in pure Python). `operonx.app.evals` becomes a package and keeps its import path.
   The score store goes in `operonx/telemetry/scores/`. Studio only renders and calls operonx APIs.

---

## 1. What exists today (read, not assumed)

### 1.1 OperonX (`/home/thanglq/Operon`, v1.14.0)

| Piece | Where | What it does today |
|---|---|---|
| `Dataset` | `operonx/app/evals.py:88-141` | JSONL file, one case per line `{id,input,expected,tags,from,note}` (`CASE_KEYS` at :63). Reads are lazy. `add()` appends and dedupes by id. A row without `input` *is* the input (:111-112). |
| Case id | `operonx/app/evals.py:80-85` | Uses the row's own `id`, otherwise `sha1(json(input))[:12]`. The id is stable across appends but changes if the input is edited. |
| `dataset:name` refs | `operonx/app/evals.py:69-77` | Resolve to `<root>/datasets/name.jsonl`. |
| Verdict normalisation | `operonx/app/evals.py:147-162` | Accepts bool, a number in [0,1] (passes at 0.5), or a dict `{passed,score,reason}`. `None` is a failure. |
| Evaluator calling | `operonx/app/evals.py:169-188` | Plain, async, or `@op` (the **body** is called through `__wrapped__`, so it is *not* traced). Arguments are injected by name from `input, output, expected, row, outputs` (:405-411). An evaluator that raises fails its own case. Each evaluator is timed. |
| Built-ins | `operonx/app/evals.py:202-273` | `exact(field)`, `contains(*needles)`, `fuzzy(threshold)` (difflib), `json_match(keys)`. |
| `llm_judge` | `operonx/app/evals.py:276-320` | Builds a one-node `GraphOp` with `LLMOp` structured output (`passed/score/reason`) and runs `Operon(g).run(inputs={})` (:306) **with no trace sinks**, so judge calls are not inspectable. Keeps `cost_usd`/`usage` on the verdict. |
| `Eval(Job)` | `operonx/app/evals.py:342-517` | Source is the dataset. A `_Capture` sink (:326-339) collects what each case sent. `judge()` (:397-437) runs the evaluators **sequentially** (:413-414), and the case passes only if all of them pass. `summarize()` (:439-473) computes cases/passed/failed/errored/pass_rate/per-check counts/p50/judge cost. A run fails if any case fails, or if `pass_rate < threshold` (point estimate, no CI). Output is clipped to 4000 chars (:520-526). |
| Manifest | `operonx/app/manifest.py:740-746`, `operonx/app/declare.py:391-392,512-536` | A `[[job]]` with `dataset` is an eval (`kind: "eval"`). `evaluators` are `module:attr` strings. |
| Judge hook in runner | `operonx/app/jobs/runner.py:326-332` | After a case's attempts, `await job.judge(raw, result)`. The live trace is available in `_attempt` (`runner.py:242`), but only `trace_id` is passed on through `ItemResult` (`record.py:76-85`). |
| Job record | `operonx/app/jobs/record.py:1-16,73-95` | `<record_dir>/<job>/<run_id>/run.json + items.jsonl`. `ItemResult.verdict` exists only for evals (:84). |
| Trace join | `operonx/app/jobs/runner.py:114-131` | Every case run carries `origin=eval, job, job_run, key` metadata, so a run store can find an eval case's trace. |
| Origins/version | `operonx/app/origin.py:44-50,90-129` | `ORIGIN_EVAL`. `code_version()` gives git `HEAD[:12]` + dirty flag. It is stamped only via `Application.bootstrap()` (`application.py:152`). `run.json` of an eval does **not** record the code version, a graph hash or a dataset hash. |
| Run store contract | `operonx/telemetry/runs/base.py:54-163` | `put_trace / list_runs / get_run / rollups / delete_runs` (+ `op_stats`, `groups`). `GROUP_FIELDS` includes `version`, `job_run` (:32-43). |
| Run model | `operonx/telemetry/runs/model.py:262-312,366-437` | `RunSummary` has `version`, `version_dirty`, `job_run`, `key`, `cost_usd`, tokens. `RunRecord.nodes` = per-op rows with `inputs/outputs/status/error/ctx/upstreams` (`rows_of_trace`, :581-624). `RunFilter` is a small data object (:378-428). |
| ClickHouse store | `operonx/telemetry/runs/clickhouse.py:1-41,109-227` | Tables `runs`, `nodes` (per execution: `op_id, op_name, op_type, ctx, inputs, outputs, status` at :155-182), `op_rollups`, `media`. ReplacingMergeTree, monthly partitions, `TTL expires_at`, versioned `schema_version` + `_migrate` (:735). Writes happen in the background and are never on the run path. |
| Retention | `operonx/telemetry/runs/retention.py:21-24` | `eval: None` means evals are kept forever. Services are kept 30 days (see `docs/guide/11-runs.md:261`). |
| Alerts | `operonx/telemetry/runs/alerts.py:36-120` | `METRICS = ("error_rate","p95_ms","cost_per_hour","runs")` over run summaries. **No score metric.** |
| Sources | `operonx/app/jobs/sources.py:50-228` | Protocol: async `items()`. JSONL/CSV/dir/Python. **No run-store source.** |
| Graph serialisation | `operonx/core/ops/graph/graph_op.py:824`, `operonx/cli/pack.py:70` | `GraphOp.serialize()` + `_scrub()` give a canonical JSON spec, which is a ready-made input for a *graph hash*. |
| LLM / tool data in traces | `operonx/providers/ops/llm.py:224` (`type="llm"`), `operonx/agents/graphs/react.py:394-447` | LLM rows carry `cost_usd`, `usage`, and `tool_calls` in outputs. Tools fan out via `dispatch` subgraphs. |
| CLI | `operonx/cli/main.py:30-35`, `operonx/cli/run.py:147-178` | `operonx run <eval>` returns exit status 1 when the run fails. **No `operonx eval` command.** |
| Tests | `tests/internal/app/test_evals.py:77-365` | 11 tests: dataset, verdicts, built-ins, judging, threshold, broken evaluator, doors, llm_judge cost, manifest, CLI gate, plain job unchanged. |
| Docs | `docs/guide/13-evals.md`, `CHANGELOG.md:588-596` | The user guide. The changelog entry for evals is under 1.9.0. |
| Telemetry scores | `grep -rn score operonx/telemetry` | **None.** Nothing writes scores to Langfuse or to any store. |

### 1.2 operonx-studio (`/home/thanglq/operonx-studio`)

| Piece | Where | What it does today |
|---|---|---|
| Evals list + detail API | `operonx_studio/app.py:2821-2842` | Lists every `kind=eval` job with its last 30 runs (`_eval_runs` :2817), plus all datasets, plus a pre-loaded detail (the one-hop rule, `docs/REFACTOR_PHASE2.md:346`). |
| One eval run | `operonx_studio/app.py:2844-2893` | Items from `items.jsonl`. The default `against` is the previous finished run. It computes **flips** (`fixed`/`regressed`) as a boolean `passed` change per case (:2880-2889) and finds `new_cases`. There are no repeats, so one flaky case shows up as a "regression". |
| Where it reads from | `operonx_studio/app.py:2191-2212` | `_job_runs` / `_run_dir` read the **local** `record_dir`. Experiments run in CI or on another host are invisible. |
| Datasets API | `operonx_studio/app.py:2782-2815,2895-2985` | Discovers `datasets/*.jsonl` plus the datasets that evals reference, and shows tag counts and `used_by`. `dataset_add` appends rows, or builds one case from a single-message playground run (`from_run`), with the egress reply optionally used as `expected` (:2936-2961). It rejects multi-turn sessions (:2937-2940). It uses operonx's own `Dataset` (:2780). |
| Review queue | `operonx_studio/review.py:32-63`, `app.py:2406-2469` | `.operonx/reviews.jsonl`, append-only, last line wins: `{run, verdict good|bad|None, labels, note, user, at}`. The queue filters runs by origin/name/status/verdict/label. |
| Conversation view | `operonx_studio/review.py:91-134` | A run read as user/bot turns, built from the playground script, `transcript` outputs, and egress door inputs. |
| Review → dataset | `operonx_studio/app.py:2471-2502` | What the user said becomes `input`. Labels plus `review:<verdict>` become tags. `from.run` links back to the source run. `expected` is left blank on purpose. |
| Run compare | `operonx_studio/app.py:1841-1866`, `static/runview.js:272` | Two **single runs**, per-op time/cost/count deltas. There is no eval-level or experiment-level comparison. |
| Run a job/eval | `operonx_studio/app.py:2287` | Starts a detached `operonx-run <name>`. |
| Evals UI | `operonx_studio/static/evals.js:33-66` (pass-rate sparkline), `:149-262` (run detail: big %, delta vs before at :166-172, Run/Against pickers, filters All/Failed/Changed at :207, regressions ranked first at :229, check chips with reasons, "Open run" to trace at :253), `:264-295` (dataset view), `:297` (run eval) | Pass/fail only. No CI, no per-metric scores, no repeats, no trajectory, no cost/latency comparison across experiments. |
| Review UI | `operonx_studio/static/review.js:1-8,138-170,181-183` | Queue, good/bad/labels/note, keys `g b j k`, "Add to dataset…". |
| Assistant tool | `operonx_studio/mcp.py:245-275` (`t_run_eval`), `:362` | Runs an eval, waits, and reports the pass rate and flips against the previous run. |
| Plans | `docs/PLATFORM_PLAN.md:467-483` (§8.1 datasets/evals: "evaluators … written as ops, so they are graphs too", "compare eval runs by version … merge gate"), `:493-499` (§8.3 review queue), `:404` (simulated user), `docs/TEAM_PLAN.md:126-133,410` (edit rights on review/dataset routes; viewers can't review). |
| Tests | `tests/studio/test_evals.py:119-191` | 5 tests: list, fix/regress flips, dataset read/append, playground-run case, assistant tool. |

### 1.3 The first real consumer: callbot QC

`educa-reminder-agent@refactor/operonx-studio:src/qc/` (`graph.py` `check_case`
/ `score_cases`) is already an eval in disguise: scripted multi-turn scenarios
and spreadsheet test cases go in, one verdict per case comes out, and a report is produced. It is
wired as a runbook (`qc_cases` → `qc_report`). Its shape is the forcing function for
**multi-turn cases** and **scenario clustering** (§8.3).

### 1.4 Gaps this track closes

| # | Gap | Evidence |
|---|---|---|
| G1 | Evaluators cannot see intermediate ops, so there is no trajectory/tool/budget evaluation | `evals.py:405-411` passes only input/output/expected/row/outputs; `runner.py:242` drops the trace |
| G2 | Verdicts live only on local disk, so Studio cannot see CI experiments and nothing can be queried across experiments | `record.py:85`, `app.py:2191-2212` |
| G3 | There is no experiment identity (code sha, graph/config hash, dataset version, evaluator version) | `evals.py:439-473` summary, `origin.py:122-129` stamping only at bootstrap |
| G4 | No repeats, so flakiness reads as regressions | `app.py:2880-2889` |
| G5 | No uncertainty: the gate is a point estimate and "+3 pts" on 30 cases is reported as a change | `evals.py:465-472`, `evals.js:166-172` |
| G6 | The judge is untraced, uncached, unversioned, with no pairwise mode and no alignment check | `evals.py:291-317` |
| G7 | Evaluators run serially per case, so judge latency adds up | `evals.py:413-414` |
| G8 | No online evaluation of production traces | no run-store source; no score store |
| G9 | Human reviews are a separate JSONL that isn't a score. Queues are only filters, with no rubric and no sampling | `review.py:32-63` |
| G10 | No `operonx eval` CLI, no reports (JUnit/MD), no pytest plugin | `cli/main.py:30-35` |
| G11 | Datasets have no version, no split, no edit/archive, and no case history | `evals.py:88-141`, `app.py:2895-2985` |
| G12 | No multi-turn conversation cases, which is callbot's main shape | `app.py:2937-2940` |
| G13 | Alerts cannot watch quality, only latency, errors and cost | `alerts.py:36` |

---

## 2. What the field does (late 2026), and what we take from it

| System | Model / idea | Adopt | Reject |
|---|---|---|---|
| **Langfuse** | Dataset → DatasetItem(`input, expectedOutput, metadata, sourceTraceId, sourceObservationId, status ACTIVE/ARCHIVED`) → DatasetRun → DatasetRunItem(`traceId`) + Scores. Score = `name, value, dataType NUMERIC/CATEGORICAL/BOOLEAN/TEXT`, `source API/EVAL/ANNOTATION`, attached to trace/observation/session/dataset run. Run-level evaluators. Online LLM-as-judge rules = filter + **sampling %** + evaluators. Annotation queues. ([data model](https://langfuse.com/docs/evaluation/experiments/data-model), [core concepts](https://langfuse.com/docs/evaluation/core-concepts), [online](https://langfuse.com/docs/evaluation/get-started/online.md)) | Score as a universal row with a target, source and data type. The source-trace link on cases. Archive instead of delete. Rule = filter + sample + evaluators. | The DB as the dataset's source of truth (we keep git JSONL). |
| **LangSmith** | Dataset/example (inputs, reference outputs, metadata), **splits**, auto **versions** + tags for CI; experiments; code / LLM-judge / pairwise / summary evaluators; repetitions; annotation queues (single + **pairwise** queues); online evaluators with sampling, filters, **spend limits**; backtesting; few-shot judge corrections; pytest plugin `@pytest.mark.langsmith` + `log_outputs`/`expect` ([concepts](https://docs.langchain.com/langsmith/evaluation-concepts), [pairwise](https://docs.smith.langchain.com/evaluation/how_to_guides/evaluate_pairwise), [pytest](https://docs.langchain.com/langsmith/pytest)) | Splits, version tags for CI, summary (experiment-level) evaluators, spend limits, backtesting (= rescoring stored traces), opt-in pytest plugin, using judge corrections as few-shot examples. | Auto-loaded plugins. |
| **Braintrust** | `Eval(data, task, scores)`; experiment comparison with per-row improved/regressed diffs; trial counts; "hill-climbing" with `BaseExperiment()` as the expected output; autoevals scorers ([write](https://braintrust.dev/docs/guides/evals/write), [analyze](https://www.braintrust.dev/foundations/how-to-analyze-your-eval-results)) | Row-level diff as the main comparison screen; base experiment; trials. | — |
| **Arize Phoenix** | Datasets of examples, **versioned on every insert/update/delete**, built from production spans; experiments; evaluators that write back onto spans ([phoenix-evals](https://arize.com/docs/ax/integrations/evaluation-integrations/phoenix-evals)) | Span-level (op-level) scores; datasets built from spans. | — |
| **Inspect AI (UK AISI)** | Task = dataset + solver + scorer; scorers emit `accuracy` + `stderr`; **epochs** with reducers `mean, median, mode, max, at_least_{n}, pass_at_{k}, pass_k_{k}`; multi-grader panels (`majority`), **Krippendorff's α** across judges; re-scoring existing logs (`inspect score`) ([scorers](https://inspect.aisi.org.uk/scorers.html), [llms-full](https://inspect.aisi.org.uk/llms-full.txt)) | Repeats with named reducers (incl. pass^k); stderr reported by default; **rescore without rerun**; judge panels (later). | — |
| **OpenAI Evals / graders** | `string_check` (eq/neq/like/ilike), `text_similarity`, `score_model`, sandboxed `python` grader; noted as being deprecated with the fine-tuning flows ([graders](https://developers.openai.com/api/docs/guides/graders)) | Grader taxonomy maps onto our built-ins. | Hosted sandboxed code graders (our evaluators run in-process, in the project's env). |
| **promptfoo** | YAML cases + assertions (`equals, contains, is-json, regex, similar, llm-rubric, g-eval, latency, cost`, custom); non-zero exit fails CI; JSON/HTML artifacts; GitHub Action comments the diff on the PR ([CI write-up](https://medium.com/@alexrodriguesj/testing-llm-prompts-like-code-regression-evals-in-ci-cd-with-promptfoo-5242b4dcb9be)) | **latency/cost as assertions** (budget evaluators); a PR/MR comment with the diff; artifacts. | YAML as the place to write evaluators (we use Python plus toml declarations). |
| **DeepEval** | pytest-native `assert_test(LLMTestCase, [metrics])`, `deepeval test run`; G-Eval (CoT rubric) ([repo](https://github.com/confident-ai/deepeval)) | Per-case pytest parametrisation. | A metric zoo in core. |
| **Ragas** | faithfulness, context precision/recall; agent metrics **ToolCallAccuracy**, **TopicAdherence** ([mlflow ragas](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/third-party/ragas/)) | Tool-call accuracy as a trajectory built-in; RAG metrics as an optional recipe in the guide. | Bundling Ragas. |
| **AgentEvals (LangChain)** | Trajectory match modes **strict / unordered / subset / superset**; LLM trajectory judge ([trajectory evals](https://docs.langchain.com/langsmith/trajectory-evals)) | The four modes, verbatim semantics, over operonx op paths *and* tool calls. | — |
| **τ-bench** | **pass^k** = all k i.i.d. trials succeed; unbiased estimate `E[C(c,k)/C(n,k)]`; GPT-4o retail drops to ~25% at pass^8 ([arXiv 2406.12045](https://arxiv.org/pdf/2406.12045)) | pass^k as the reliability metric for agents and conversations. | — |
| **Anthropic, "Adding Error Bars to Evals"** | Report SEM / 95% CI; **clustered SE** (can be >3× naive); resample to cut within-question variance; **paired differences**; **power analysis** ([post](https://www.anthropic.com/research/statistical-approach-to-model-evals), [arXiv 2411.00640](https://arxiv.org/pdf/2411.00640)) | All five, as the statistics layer (§8). | — |
| **LLM-as-judge literature** | MT-Bench: GPT-4 judge ≥80% agreement with humans; position, verbosity and self-enhancement biases; mitigate with **position swap** and reference-guided grading ([arXiv 2306.05685](https://arxiv.org/pdf/2306.05685)). Calibrate on 100–200 human-labelled examples; κ>0.6 acceptable, >0.8 strong ([practice notes](https://levelup.gitconnected.com/llm-as-a-judge-calibration-cohens-kappa-and-judge-bias-in-production-e8e7b58ba064), [bias mitigation study](https://arxiv.org/pdf/2604.23178)). Binary pass/fail per failure mode beats Likert; validate with TPR/TNR ([Husain evals skills](https://skills.sh/hamelsmu/evals-skills/eval-audit)). **Criteria drift**: grading outputs changes the criteria, so judges need iterative human alignment ([EvalGen, arXiv 2404.12272](https://arxiv.org/pdf/2404.12272)) | Binary default; one failure mode per judge; swap; reference-guided; alignment record with κ/TPR/TNR; disagreements become few-shot examples; rubric versioning. | 1–10 scales as default; gating on unaligned judges without a warning. |
| **OpenTelemetry GenAI** | `gen_ai.evaluation.result` event, parented to the evaluated span; conventions still *Development* as of Aug 2026 ([overview](https://www.truefoundry.com/blog/opentelemetry-genai-semantic-conventions)) | Score fields chosen to map 1:1 onto it (name, value, label, explanation, parent span = op_id). Export later. | Depending on it now. |

---

## 3. Design principles

1. **No new runtime.** Experiments, online eval and rescoring are all Jobs
   (`operonx/app/jobs`). They get resume, concurrency, retries, records and
   `origin` tags for free.
2. **One score row for everything:** code checks, judges, humans, API, online,
   offline. Different screens are filters over the same table.
3. **Everything is joinable through the trace id.** A case → its trace → its ops →
   its scores → the judge's own trace. Studio drill-down is a series of joins, never
   copies.
4. **Numbers come with error bars, or they are not shown as a change.**
5. **Cheap things stay cheap.** Deterministic evaluators stay plain function
   calls with no tracing overhead. Only judges (LLM calls) become traced graphs.
6. **Datasets are code.** JSONL in git, reviewed in MRs, versioned by content
   hash. Studio edits them the way it edits other project files.
7. **Evaluators are portable.** The same evaluator runs offline (live trace),
   in rescore (stored trace) and online (stored trace, no `expected`).

---

## 4. The core flow

```
          git: datasets/*.jsonl (version = content hash)
                     │ cases (id, input, expected, tags, split, cluster, turns?)
                     ▼
 ┌──────────── Experiment = Eval(Job) run, origin=eval ─────────────┐
 │  fingerprint: code sha+dirty · graph hash · config hash ·        │
 │               dataset version · evaluator versions · operonx ver │
 │  for case × repeat r:                                            │
 │     graph run ──► WorkflowTrace ──► run store (runs/nodes/rollups)│
 │                         │                                        │
 │                    TraceView(rows)                               │
 │                         ▼                                        │
 │     evaluators (concurrent per case; judges are traced graphs)   │
 │        └─► Verdict ─► Score rows (item / trace / op targets)     │
 └──────────────────────────────┬───────────────────────────────────┘
                                ▼
   Metrics: per evaluator mean ± CI (Wilson / clustered SE), pass^k,
            cost, latency p50/p95, error rate, per tag/split slices
                                ▼
   Comparison vs baseline (same dataset version ∩ case ids):
            paired diff + bootstrap CI, McNemar on binary checks,
            case flips classified by stability across repeats,
            per-op cost/latency deltas (op_rollups by job_run)
                                ▼
   Regression detection → Gate: pass | regressed | inconclusive
            (+ must-pass tier, + max error rate → infra failure)
                                ▼
   Report: run.json + ScoreStore rows; md / json / junit; Studio pages;
           exit code for CI; MR comment
```

The same back half (TraceView → evaluators → scores → metrics → alerts) runs
for **online** eval. The only differences are that the source is the run store and no `expected` is available.

---

## 5. Data model

### 5.1 Entities

**Dataset** (file): `name`, `path`, `version` = `sha256` over the canonical JSON of
the active cases sorted by id (12 hex chars shown). Datasets also keep a `git` sha when
the file is committed and clean.

**Case** (one JSONL line; existing keys keep their meaning):

| Field | Type | Notes |
|---|---|---|
| `id` | str | Existing rule (`evals.py:80-85`). |
| `input` | any | What the graph receives (existing). |
| `expected` | any? | Reference output (existing). |
| `tags` | list[str] | Existing; also used for slices and `critical` must-pass. |
| `split` | str? | **new**, e.g. `dev` / `test` / `smoke`; `--split` selects. |
| `cluster` | str? | **new**, the scenario/conversation group used for clustered SE (defaults to `id`). |
| `trajectory` | obj? | **new**, the reference for trajectory evaluators: `{"ops": [...], "tool_calls": [{"name","args"}]}`. |
| `turns` | list? | **new**, a multi-turn script `[{"user": "...", "expect": {...}}]`, or `{"persona": "...", "goal": "...", "max_turns": 12}` for a simulated user (§8.3). |
| `from` | obj | Existing `{run, origin, name}`; **adds** `op_id` when made from one op. |
| `status` | `active`/`archived` | **new**, archive instead of delete so history stays readable. |
| `note`, `metadata` | | Existing / free-form. |

**Experiment** (= one `Eval` run; `run.json` + `experiments` row):
`experiment_id` (= job `run_id`, `record.py:66-73`), `eval`, `project`, `dataset`,
`dataset_version`, `split`, `graph`, **fingerprint** {`code_version`,
`version_dirty`, `graph_hash`, `config_hash`, `evaluators_hash`,
`operonx_version`}, `variant` (free label: "gpt-5-mini prompt v3"), `repeats`,
`baseline_id`, `status`, `started_at`, `ended_at`, `cases`, `errored`,
`metrics` (per evaluator: `n, mean, ci_lo, ci_hi, se, method`), `gate`
(verdict + reasons), `cost_usd` (system), `judge_cost_usd`, `p50_ms`, `p95_ms`.

**ExperimentItem** (= `ItemResult`, one per case × repeat): `experiment_id`,
`case_id`, `case_hash` (hash of input+expected, so an edited `expected` is detected),
`repeat`, `trace_id`, `status`, `ms`, `cost_usd`, `tokens`, `output` (clipped), `error`,
`tags`, `cluster`, `passed` (all checks).

**Score** (universal; maps onto Langfuse score / OTel `gen_ai.evaluation.result`):

| Field | Notes |
|---|---|
| `score_id` | Deterministic, which makes it idempotent (see §6.3). |
| `target` | `item` / `trace` / `op` / `session` / `pair` |
| `trace_id`, `op_id?`, `session_id?` | What was judged. `op_id` = the blamed or evaluated execution. |
| `experiment_id?`, `case_id?`, `repeat?` | Set for offline scores. |
| `pair_experiment_id?` | For pairwise scores (target `pair`). |
| `origin`, `name` | Of the judged run (`service:call`, `eval:replies`), denormalised for trend queries. |
| `score_name` | Evaluator name, e.g. `exact(intent)`, `judge:polite`, `review`. |
| `evaluator_version` | Hash of the evaluator source / rubric + judge model + judge graph hash. |
| `source` | `code` / `judge` / `human` / `api` |
| `data_type` | `bool` / `numeric` / `categorical` |
| `value` (float?), `passed` (bool?), `label` (str?) | Bools also store `value` 0/1, so means work. |
| `reason` | Explanation (ZSTD). |
| `judge_trace_id?`, `cost_usd?` | A judge's own trace and its spend. |
| `author?`, `rule?`, `queue?` | Human reviewer; online rule; annotation queue. |
| `snapshot?` | Clipped input/output for online targets, so the score stays readable after its trace expires (services keep traces 30 days). |
| `created_at`, `metadata` | |

**OnlineRule** (declared in `operonx.toml`, not a table): `name`, `runs`
(a `RunFilter`), `sample`, `evaluators`, `schedule`, `budget_usd_per_day`,
`queue` (where failures go), `target` (`trace` or `session`).

**Queue** (declared in `operonx.toml` or created in Studio, then saved there):
`name`, `filter` (run filter and/or score predicate, e.g. `judge:polite = fail`),
`sample`, `rubric` (score configs: names, types, allowed labels), `assignees?`.

**Score config** (borrowed from Langfuse): named human-score schemas
(`review: categorical good|bad`, `resolved: bool`, `tone: categorical
warm|neutral|cold`). They keep human labels comparable to judge labels for alignment.

### 5.2 The fingerprint (experiment identity)

| Component | How | Catches |
|---|---|---|
| `code_version`, `version_dirty` | `origin.code_version(root)` (`origin.py:90-119`), now also stamped by `Eval` itself | Any code change |
| `graph_hash` | `sha256(canonical_json(_scrub(graph.serialize())))` (`graph_op.py:824`, `pack.py:70`) | Topology, literal params, inline prompts, even with a dirty tree |
| `config_hash` | Hash of the resolved resources the graph uses (`llm:*` model, temperature, base_url host), **with secrets scrubbed**, plus prompt files referenced by path | Model swaps done in YAML |
| `dataset_version` | §5.1 | Changed cases |
| `evaluators_hash` | Sorted evaluator versions | A rubric edit (otherwise a "regression" could be a stricter judge) |
| `operonx_version` | `operonx.__version__` | Engine upgrades |

Two experiments are **directly comparable** when `dataset_version` and
`evaluators_hash` match. Otherwise the comparison runs on the case-id intersection, excludes
cases whose `case_hash` changed, and the UI says so. Changes to the system under test (code, graph,
config) are what a comparison is *meant* to measure.

---

## 6. Storage

### 6.1 Contract: `ScoreStore` (new, `operonx/telemetry/scores/`)

The run store contract was deliberately kept to five methods (`runs/base.py:1-16`), so scores get their
own small contract instead of a sixth through tenth method on `RunStore`. The two share
configuration and backends, and one ClickHouse database can hold both.

```python
class ScoreStore(ABC):
    # experiments
    def put_experiment(self, exp: Experiment) -> None: ...          # upsert (status changes)
    def put_items(self, items: Sequence[ExperimentItem]) -> None: ...
    def list_experiments(self, where: ExperimentFilter, limit=50, cursor=None) -> Page: ...
    def get_experiment(self, experiment_id: str) -> Optional[ExperimentRecord]: ...  # + items
    # scores
    def put_scores(self, scores: Sequence[Score]) -> None: ...      # idempotent by score_id
    def scores(self, where: ScoreFilter) -> List[Score]: ...         # by trace/experiment/name/time
    def score_series(self, where: ScoreFilter, bucket_s: int) -> List[Bucket]: ...  # trends
    # judge cache
    def cache_get(self, key: str) -> Optional[dict]: ...
    def cache_put(self, key: str, verdict: dict) -> None: ...
```

Resolution mirrors `project_stores` (`runs/project.py:1-80`): a `[tracing]` sink
`trace_clickhouse:x` means the scores live in the same ClickHouse DB. `local` means
files+SQLite under the runs root. `[evals] store = "score_store:<n>"` overrides.

### 6.2 Files + SQLite (default, zero setup)

* Experiments **remain job records**. `run.json` gains `fingerprint`, `metrics`,
  `gate`, and `items.jsonl` gains `repeat`, `case_hash`, and `scores` (the existing
  `verdict` is kept, so the current Studio code keeps working).
* Online, human and API scores go to `<runs root>/scores/YYYY-MM.jsonl` (append-only, last
  `score_id` wins). `reviews.jsonl` is read as `source=human` scores, then migrated once (§10.4).
* Indexing: tables `experiments`, `experiment_items`, `scores` in the existing
  `<root>/.index.sqlite` (`runs/files.py:1-14`), refreshed the same way `FilesRunStore.refresh` indexes run dirs.

### 6.3 ClickHouse (teams, CI, online), schema version 3

These tables are added by `_migrate` (`runs/clickhouse.py:735`) in the same database:

```sql
CREATE TABLE IF NOT EXISTS {db}.experiments (
  experiment_id String, project LowCardinality(String), eval LowCardinality(String),
  dataset LowCardinality(String), dataset_version String, split LowCardinality(Nullable(String)),
  graph LowCardinality(String), code_version LowCardinality(Nullable(String)),
  version_dirty Nullable(Bool), graph_hash String, config_hash String, evaluators_hash String,
  operonx_version LowCardinality(String), variant Nullable(String), repeats UInt16,
  baseline_id Nullable(String), status LowCardinality(String),
  started_at Float64, ended_at Nullable(Float64), cases UInt32, errored UInt32,
  cost_usd Nullable(Float64), judge_cost_usd Nullable(Float64),
  p50_ms Nullable(Float64), p95_ms Nullable(Float64),
  metrics String CODEC(ZSTD(3)),      -- {evaluator: {n, mean, se, ci_lo, ci_hi, method}}
  gate String CODEC(ZSTD(3)),         -- {verdict, reasons[], baseline_id, tests[]}
  metadata String CODEC(ZSTD(3)),
  written_at DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree(written_at)
ORDER BY (project, eval, started_at, experiment_id);   -- no TTL: evals are kept forever

CREATE TABLE IF NOT EXISTS {db}.experiment_items (
  experiment_id String, case_id String, repeat UInt16, case_hash String,
  trace_id Nullable(String), status LowCardinality(String), ms Float64,
  cost_usd Nullable(Float64), tokens_in UInt64, tokens_out UInt64,
  passed Nullable(Bool), tags Array(LowCardinality(String)), cluster Nullable(String),
  output String CODEC(ZSTD(3)), error Nullable(String) CODEC(ZSTD(3)),
  written_at DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree(written_at)
ORDER BY (experiment_id, case_id, repeat);

CREATE TABLE IF NOT EXISTS {db}.scores (
  score_id String, target LowCardinality(String),
  trace_id Nullable(String), op_id Nullable(String), session_id Nullable(String),
  experiment_id Nullable(String), pair_experiment_id Nullable(String),
  case_id Nullable(String), repeat Nullable(UInt16),
  origin LowCardinality(String), name LowCardinality(String),
  score_name LowCardinality(String), evaluator_version String,
  source LowCardinality(String), data_type LowCardinality(String),
  value Nullable(Float64), passed Nullable(Bool), label LowCardinality(Nullable(String)),
  reason String CODEC(ZSTD(3)), judge_trace_id Nullable(String), cost_usd Nullable(Float64),
  author LowCardinality(Nullable(String)), rule LowCardinality(Nullable(String)),
  queue LowCardinality(Nullable(String)), snapshot String CODEC(ZSTD(3)),
  metadata String CODEC(ZSTD(3)), created_at Float64, expires_at DateTime,
  written_at DateTime64(3) DEFAULT now64(3),
  INDEX by_trace trace_id TYPE bloom_filter GRANULARITY 4,
  INDEX by_exp experiment_id TYPE bloom_filter GRANULARITY 4
) ENGINE = ReplacingMergeTree(written_at)
PARTITION BY toYYYYMM(toDateTime(created_at))
ORDER BY (origin, name, score_name, created_at, score_id)   -- trend queries are the hot path
TTL expires_at;

CREATE TABLE IF NOT EXISTS {db}.judge_cache (
  key String, verdict String CODEC(ZSTD(3)), created DateTime DEFAULT now(), expires_at DateTime
) ENGINE = ReplacingMergeTree(created) ORDER BY key TTL expires_at;
```

**Idempotent ids**, so a retried batch or a re-run online job collapses
(ReplacingMergeTree, same trick as `runs`):

* offline: `sha(experiment_id, case_id, repeat, score_name)`
* online/rescore: `sha(trace_id, op_id?, score_name, evaluator_version)`
* human: `sha(target ids, score_name, author)`, so an edit replaces the old value and "last wins" is kept
* pairwise: `sha(experiment_a, experiment_b, case_id, score_name)`

**Retention:** eval and human scores are kept forever. Online scores keep 365 days by default
(`[evals] online_ttl_days`), which is longer than service traces (30 d). This is why
`snapshot` exists. Scores reuse the background writer (`telemetry/writer.py`), so a
down ClickHouse never blocks an experiment, and the local job record stays the
fallback truth.

---

## 7. Evaluators

### 7.1 Interface (backward compatible)

The existing contract stays: a plain/async function or an `@op`, with arguments injected
by name, returning bool, a number, or a dict (`evals.py:147-188`). This track adds:

* **New injectable arguments:** `trace` (a `TraceView`), `case` (the full case),
  `repeat`, `experiment` (fingerprint + variant), `outputs` (existing).
* **A richer return value**, which is optional. `Verdict` is a dataclass equal to the dict:
  `passed, score, label, reason, op` (blame: op name or op_id → a Studio highlight),
  `metrics` (extra named numbers, e.g. `{"recall@5": 0.8}`), `cost_usd`,
  `judge_trace_id`. Returning a **list** of verdicts with distinct `name`s
  lets one evaluator emit several scores.
* **An optional decorator** for metadata that other features rely on:

```python
@evaluator(name="slot_grounded", version="2", needs=("trace",), reference_free=True,
           kind="code", gate=True)
def slot_grounded(output, trace: TraceView) -> Verdict: ...
```

`reference_free=True` (meaning it does not use `expected`) is what lets an evaluator be used
**online**. It is inferred when the function has no `expected` or `case` parameter.
`version` defaults to a hash of the function source (`inspect.getsource`).
Changing an evaluator therefore changes `evaluators_hash`.

* **Concurrency (G7):** a case's evaluators run with `asyncio.gather`. Judges
  share a separate `judge_concurrency` semaphore (default 8), so a 200-case eval
  doesn't flood the judge endpoint.

### 7.2 `TraceView`: trajectory and intermediate evaluation (G1)

```python
class TraceView:
    trace_id: str; metadata: dict; duration_ms: float; cost_usd: float | None
    @classmethod
    def from_trace(cls, trace) -> "TraceView"          # offline: rows_of_trace(trace, consumer)
    @classmethod
    def from_rows(cls, rows, meta) -> "TraceView"       # online / rescore: RunRecord.nodes
    @classmethod
    def from_store(cls, store, trace_id) -> "TraceView"
    def ops(self, name=None, *, type=None, under=None, status=None) -> list[OpRow]
    def first(self, name) -> OpRow | None; def last(self, name) -> OpRow | None
    def path(self, *, types=None, collapse=False, depth=None) -> list[str]   # executed op names in start order
    def llm_calls(self) -> list[OpRow]                  # op_type == "llm" (providers/ops/llm.py:224)
    def tool_calls(self) -> list[ToolCall]              # from llm outputs["tool_calls"] + dispatch results
    def errors(self) -> list[OpRow]
    def turns(self) -> list[Turn]                       # studio's conversation rules, moved into operonx
```

`OpRow` = `op_id, op_name, op_full_name, op_type, ctx, inputs, outputs, status,
error, start, duration_ms, cost_usd, tokens`. These are the row fields every store already
keeps (`runs/model.py:594-624`; ClickHouse `nodes` at `clickhouse.py:155-182`).
Building the view from `rows_of_trace` means **offline and online evaluators read
exactly the same shape**. That property is the main point of this subsection, and Phase 2 tests it as a golden equivalence.

Plumbing change: `runner._attempt` attaches the settled live trace to the
`ItemResult` as a transient, non-serialised field (`runner.py:242`). `Eval.judge`
builds the `TraceView` lazily, so this costs nothing unless an evaluator asks for `trace`.
`turns()` moves the conversation reader from `operonx_studio/review.py:91-134`
into operonx, so Studio and evaluators share the same rules.

### 7.3 Built-in evaluators

| Family | Built-ins (factories) | Notes |
|---|---|---|
| Deterministic (existing) | `exact`, `contains`, `fuzzy`, `json_match` | Unchanged. |
| Deterministic (new) | `regex(pattern, field)`, `json_schema(schema)`, `one_of(labels, field)`, `numeric_close(tol, field)` | Map onto OpenAI `string_check` / promptfoo `is-json` / `regex`. |
| **Trajectory** | `trajectory.ops(reference=None, mode="strict"\|"unordered"\|"subset"\|"superset", types=None)` | AgentEvals semantics over `trace.path()`. The reference comes from the argument or `case.trajectory.ops`. |
| | `trajectory.tool_calls(mode=…, args="exact"\|"subset"\|"ignore")` | Ragas ToolCallAccuracy-like; score = share of reference calls matched. |
| | `trajectory.op_output(op, check)` | Applies any evaluator to one op's output (e.g. `op_output("classify", exact("intent"))`), which is the intermediate-step test. |
| | `trajectory.no_errors()`, `trajectory.max_steps(n, op=None)` | Catches loops and runaway agents. |
| **Budget** | `budget(ms=None, cost_usd=None, llm_calls=None, tokens=None)` | promptfoo's `latency`/`cost` assertions; reads the TraceView totals. |
| **Judge** | `judge(llm, rubric, *, pass_label="PASS", reference=False, examples=None, name)` | Replaces `llm_judge`, which is kept as an alias. Binary by default. See §7.5. |
| **Pairwise** | `pairwise(llm, rubric, *, swap=True)` | Only in `compare`; see §7.5. |
| **Summary** (experiment-level) | functions taking `items` (all case results) and returning metrics, e.g. `f1_over_labels("intent")`, `confusion("intent")` | LangSmith summary / Langfuse run evaluators. Stored as experiment metrics. |
| Conversation | `conversation.goal_reached(llm, goal_from="case.turns.goal")`, `conversation.turn_checks()` (per-turn `expect`) | §8.3 / Later. |

Domain metrics (RAG faithfulness, BLEU…) are **guide recipes**, not core
built-ins (see Avoid).

### 7.4 Evaluators *are* operonx ops/graphs (`PLATFORM_PLAN.md:476-477`)

* A **plain function or `@op`** is called for its body, as today (`evals.py:170`), with no
  engine and no trace. This keeps deterministic checks at microsecond cost.
* A **`@graph` evaluator** (or any `GraphOp`) runs through `Operon(g, trace=<eval
  sinks>)` with metadata `origin=eval, job=<eval>, job_run=<exp>, role=judge,
  judged_trace=<trace_id>, case=<id>`. It receives the same named inputs, plus
  `trace_summary` (a compact text rendering of `TraceView`, for LLM trajectory
  judges), and returns `{passed, score, label, reason}`. Its trace id becomes
  `judge_trace_id`, and Studio's "why did the judge say that" is one click.
* The built-in `judge()` *is* such a graph: `render_prompt → LLMOp(structured) →
  parse`. `pairwise()` is a graph with **two parallel branches** (A,B) and (B,A)
  and a reconcile op. A judge panel (Later) is a fan-out over `llm:` resources
  plus a majority reducer. These are natural operonx shapes, and they let the engine's
  concurrency, retries, cost accounting and tracing do the work.
* Evaluator graphs can be declared in the manifest like any graph
  (`evaluators = ["judges:polite"]`), opened on the Studio canvas, and edited in
  the prompt workbench (`PLATFORM_PLAN.md:485-491`).

### 7.5 LLM-as-judge rules (encoded, not just documented)

1. **Binary per failure mode by default.** `judge()` asks for `PASS`/`FAIL` + reason.
   `numeric` and `categorical` are opt-in. One judge checks one criterion (Husain; EvalGen).
2. **Rubric is a file or a string, and it is versioned.** `evaluator_version = sha(rubric,
   examples, model, temperature, judge graph hash)`. Temperature defaults to 0.
3. **Reference-guided when `expected` exists** (`reference=True`), following MT-Bench's mitigation.
4. **Position swap** for pairwise. If the verdict flips with order, it is recorded as `tie` with
   `metadata.inconsistent=true`, and the swap-inconsistency rate is reported per judge
   (a direct measure of position bias).
5. **Self-preference guard.** The judge resource is warned about when it equals an `llm:` resource
   inside the system under test (from `config_hash` inputs).
6. **Cache.** `key = sha(evaluator_version, canonical(input, output, expected,
   trace_summary?))` lives in `judge_cache`. Rescoring an unchanged output costs $0. Unchanged
   cases across experiments, which is common with deterministic SUT paths, are free.
7. **Alignment record.** `operonx eval align <judge>` joins judge scores with
   human scores on the same targets (same `score_name`, or mapped through `[evals.align]`).
   It computes **Cohen's κ, TPR, TNR, accuracy, n**, plus the confusion matrix, and stores it as a
   summary score on the judge (`target=evaluator`). Gate behaviour: a judge with **no
   alignment record or κ < 0.6** can still gate, but every report carries a
   loud "unvalidated judge" warning (thresholds from the calibration literature
   above). Disagreements are one click to **few-shot examples** in the rubric
   file (LangSmith's "corrections"). That edit bumps the version, which is criteria drift made explicit.
8. **Judge cost is reported separately** from system cost (`judge_cost_usd`
   exists today, `evals.py:419-421,463`).

---

## 8. Metrics, statistics, regression detection

All statistics live in `operonx/app/evals/stats.py`, written in pure Python (core has no
numpy, see `pyproject.toml` dependencies) and seeded so results are deterministic.

### 8.1 Per-experiment metrics

For each evaluator `e`, the case score is `s_i` = the mean over repeats of the case's
value (bool → 0/1). The reported mean is `mean_i(s_i)`, with:

* **binary and no clustering:** a Wilson 95% interval (behaves well at 0/1 and small n).
  Example: 45/50 → 90% [78.6%, 95.7%]; 43/50 → 86% [73.8%, 93.0%]. These
  overlap heavily, so Studio must *not* paint "−4 pts" red without a
  paired test.
* **numeric, or clustered:** CLT SEM. When cases carry `cluster` (callbot scenarios,
  turns of one conversation), the **clustered SE** is
  `sqrt( Σ_c (Σ_{i∈c} (s_i − s̄))² ) / n` (the Anthropic paper's recommendation; it can be over 3× the naive SE).
* **Reliability across repeats:** `pass@1` (mean), **`pass^k`** (unbiased
  `C(c,k)/C(n,k)` averaged over cases; e.g. 4 of 5 trials passing gives pass^3 = 0.4),
  and the **flaky share** (cases with 0 < passes < repeats).
* **Operational:** system `cost_usd` per case, `p50/p95 ms`, LLM calls, tokens,
  **error rate** (infra), and judge cost.
* **Slices:** every metric per `tag` and `split`. These are exploratory (see §8.4).

### 8.2 Comparing two experiments (paired)

Compare on the case-id intersection, excluding changed `case_hash`. For each
gated metric, `d_i = s_i(B) − s_i(A)`:

* point estimate `mean(d)`, **paired bootstrap 95% CI** (resample cases, or whole
  clusters when `cluster` is set; B = 2000; seed = hash(exp ids)),
* for binary checks, an **exact McNemar** test on discordant cases (b = pass→fail,
  c = fail→pass): `p = 2·P(X ≤ min(b,c) | Bin(b+c, ½))`. Example: 8 regressed
  / 1 fixed → p = 0.039,
* also reported: the correlation of A and B scores, which explains why paired is tighter than
  unpaired (Anthropic's recommendation 4).

**Case flips are classified by stability**, which fixes the flaky-regression problem
from `app.py:2880-2889`:

| A (over repeats) | B | Class |
|---|---|---|
| all pass | all fail | **regressed** |
| all fail | all pass | **fixed** |
| all pass | some fail | **destabilised** |
| flaky | flaky | noise (hidden by default) |
| some fail | all pass | stabilised |

With `repeats=1` a flip is shown as "changed (unverified)". Studio offers
"re-run these N cases ×5" to confirm, as a targeted `--cases` experiment.

### 8.3 The gate: three states (+ infra)

```
for each gated metric m (declared, not every slice):
    absolute:   mean_B(m) < threshold(m)                         → FAIL (today's semantics, kept)
    vs baseline: CI_hi(d_m) < 0  and  mean(d_m) < −tolerance(m)   → REGRESSED   (confident & material)
                 CI_lo(d_m) < −tolerance(m) ≤ … (CI straddles)    → INCONCLUSIVE
                 otherwise                                        → PASS
must-pass tier: any case tagged `critical` that passed in baseline and fails in B
                (all repeats)                                     → REGRESSED, no statistics
infra:      error rate > max_error_rate (default 5%)              → ERROR (exit 3)
```

Exit codes: `0` pass, `1` failed/regressed, `2` inconclusive (only with
`--strict`; otherwise exit 0 with a warning in the report), `3` infra error. Separating
3 from 1 matters in CI. "The LLM endpoint was down" is not "the prompt got worse".
Gating uses the experiment's `gate` block. `threshold` alone keeps the 1.9.0 behaviour exactly
(`evals.py:465-472`), so existing evals and tests are unchanged.

Per-metric `tolerance` defaults to the **measured noise floor** (§8.5), never a guess.

### 8.4 Multiple comparisons

Only **declared** gate metrics gate, and they are Holm–Bonferroni-adjusted across
themselves. Slices (per tag/split) and non-gated evaluators are shown with
Benjamini–Hochberg flags and labelled "exploratory". This avoids the
"one of 40 slices is always red" failure.

### 8.5 Noise floor and power (evidence before tuning)

* `operonx eval calibrate <eval> --runs 3`: runs the *same* fingerprint k times (an A/A test).
  It stores the per-metric run-to-run SD and the flaky share, and proposes `tolerance`
  and `repeats`. A/A comparisons must come out `PASS` in ≥95% of cases. If they
  don't, the eval is too noisy to gate, and the report says so.
* `operonx eval power <eval> --delta 0.05`: the paired sample size from the observed
  discordance rate `p_d`:
  `n ≈ (z_{α/2}·√p_d + z_β·√(p_d − δ²))² / δ²`. With p_d = 0.10, δ = 0.05, α = 0.05,
  power 0.8, that gives **≈ 312 cases**. This is the honest answer to "is my 50-case dataset
  enough to detect a 5-point drop?" (no).

### 8.6 Multi-turn and conversations (callbot)

A case with `turns` is played through the service's doors with the playground
bridge's session driver (scripted turns), or through the simulated-user driver
(`operonx/app/play.py:41-44`, `persona`), as one session = one trace
(callbot: trace id = call id). Evaluators get `trace.turns()`. Scores can target
`session`. `cluster` = scenario id. Callbot's QC graph (§1.3) ports as:
`check_case` → an evaluator graph over turns, `score_cases` → a summary
evaluator. Callbot itself follows on its own branch (`refactor/operonx-studio`).

---

## 9. Online evaluation (production traces)

```toml
[[online_eval]]
name        = "call_quality"
runs        = { origin = "service", name = "call" }   # a RunFilter (runs/model.py:378)
sample      = 0.05                                    # deterministic: hash(trace_id) < 5%
target      = "session"                               # or "trace"
evaluators  = ["judges:polite", "evals:no_dead_air", "evals:handoff_when_asked"]
schedule    = "*/10 * * * *"
budget_usd_per_day = 2.0
queue       = { when = "any_failed", to = "call_failures", sample = 0.5 }
```

* **Mechanics:** `OnlineEval(Job)` with a new **`RunStoreSource`** (`kind: runs`,
  the source protocol at `jobs/sources.py:50`). It pages `list_runs(filter, since=cursor)`,
  applies **stable sampling** (`int(sha1(trace_id)[:8],16)/2³² < rate`, so a re-run, a backfill
  or a second worker picks the same traces), loads each `RunRecord`, and builds
  `TraceView.from_rows`. It runs reference-free evaluators only (others are refused at
  declare time) and writes `trace`/`session` scores with `rule=name`. The cursor lives in the
  online job's `run.json`, and idempotent `score_id`s make overlap harmless.
* **Never inline.** Evaluators never run on the service's path. Callbot's
  latency budget is measured in tens of ms, and the run store already decouples writes
  (`clickhouse.py:1-10`).
* **Budget:** spend is tracked through judge `cost_usd`. Hitting the daily budget stops sampling
  for the day and records a `budget_exhausted` item status (LangSmith-style spend limits).
* **Backfill / backtest:** `operonx eval online backfill call_quality --since 7d`.
  A new judge can be tried on last week's traffic before it's switched on.
* **Feeding humans:** failing (or randomly sampled) targets are enqueued to a
  review queue (§10). Their human labels are what judge alignment (§7.5) is computed from.
* **Alerts (G13):** `alerts.METRICS` (`alerts.py:36`) gains
  `score_mean:<score_name>` and `score_fail_rate:<score_name>`, evaluated over the
  `scores` table in the alert window, with the same webhook delivery.
* **Trends:** `score_series` gives buckets of mean ± CI per score name and
  `version`, so a deploy (new `version`) shows as a step in the series.

---

## 10. Human review and annotation queues → datasets

### 10.1 Today
Review = good/bad/labels/note per run (`review.py:32-63`). The queue is a run filter
(`app.py:2406-2439`). "Add to dataset" copies the user's words (`app.py:2471-2502`).

### 10.2 Proposed
* A **review is a score** (`source=human, score_name="review", data_type=categorical,
  label=good|bad`, labels → `metadata.labels`, note → `reason`, `author`). Other
  rubric items from the queue's score configs are additional human scores on the
  same target.
* **Queues** = filter + sampling + rubric, declared in `operonx.toml` (`[[queue]]`)
  or created in Studio, which writes them there. Sources: a run filter; a score predicate
  ("judge:polite failed"); eval failures ("failed cases of experiment X");
  online-rule spillover (§9); random sampling for unbiased estimates.
  Each item is a `(target, queue)` pair. Done = has a human score from the queue's rubric.
* **Two reviewers on the same item** let inter-annotator κ be reported per queue. If humans disagree with
  each other, judge alignment against them is meaningless. That is reported, not hidden.
* **Pairwise queues** (LangSmith): for `compare` of two experiments, reviewers pick
  A/B/tie on shuffled, side-by-side outputs. These are pairwise human scores.
* **To dataset:** the existing route remains. Additions: (a) edit `expected` inline *before*
  saving (a corrected answer is a real reference); (b) `from.op_id` when the case
  is cut at one op (unit-test an LLM op on a real input; the prompt workbench at
  `app.py:2511` already collects op samples); (c) the split is picked on add.

### 10.3 Judge alignment screen
Covered in §7.5 (confusion matrix, κ, TPR/TNR, the disagreements list, and "add as few-shot example").

### 10.4 Migration
`reviews.jsonl` is read as human scores immediately (no data loss). A one-time
`operonx eval migrate-reviews` writes them into the score store. Studio's
`ReviewLog` becomes a thin adapter over `ScoreStore`.

---

## 11. Python API sketches

```python
from operonx.app.evals import (Eval, Dataset, Gate, evaluator, Verdict, TraceView,
                               exact, judge, budget, trajectory, pairwise)

ev = Eval(
    "replies",
    graph="bot:reply_flow",
    dataset="dataset:replies",            # datasets/replies.jsonl; version = content hash
    split="test",
    evaluators=[
        exact("intent"),
        trajectory.ops(["classify", "retrieve", "answer"], mode="subset"),
        trajectory.tool_calls(mode="unordered", args="subset"),
        budget(ms=1500, cost_usd=0.002, llm_calls=3),
        judge("llm:judge", rubric="judges/polite.md", reference=True),
    ],
    repeats=3,
    cluster="scenario",                   # case field → clustered SE / cluster bootstrap
    gate=Gate(
        threshold={"exact(intent)": 0.95},           # absolute (1.9.0 semantics)
        baseline="main",                             # main | latest | <experiment_id> | git:<ref>
        tolerance="calibrated",                      # or {"judge:polite": 0.03}
        must_pass_tag="critical",
        max_error_rate=0.05,
    ),
    variant="prompt v7",
    concurrency=8, judge_concurrency=8,  # plus every Job argument
)
exp = await ev.run()                      # JobRun; exp.meta["eval"] has metrics + gate
print(exp.meta["eval"]["gate"]["verdict"])

@evaluator(version="1")
def slot_grounded(output, trace: TraceView) -> Verdict:
    """The time the bot says must be the time the extract op saw."""
    ex = trace.last("extract_slots")
    said = (output or {}).get("time")
    ok = ex is not None and said == ex.outputs.get("time")
    return Verdict(passed=ok, reason=None if ok else f"said {said}, extracted "
                   f"{ex and ex.outputs.get('time')}", op=ex and ex.op_id)

# comparison and rescoring as library calls (the CLI and Studio use these)
from operonx.app.evals import compare, rescore, open_score_store
store = open_score_store(".")                         # same resolution as project_stores
cmp = compare(store, a="20261004T101500-000001", b="20261004T113000-000002",
              pairwise=[pairwise("llm:judge", "judges/better_reply.md")])
print(cmp.verdict, cmp.metrics["judge:polite"].diff, cmp.flips["regressed"])
new = await rescore(store, experiment="…", evaluators=[slot_grounded])  # no graph re-run

# online
from operonx.app.evals import OnlineEval
OnlineEval("call_quality", runs={"origin": "service", "name": "call"}, sample=0.05,
           evaluators=[judge("llm:judge", "judges/polite.md")], budget_usd_per_day=2.0)
```

---

## 12. CLI: `operonx eval`

A new delegated subcommand (`cli/main.py:30-35` `DELEGATED`), module `operonx/cli/eval.py`.

```
operonx eval list                                   # evals, datasets (version, cases), last gate
operonx eval run <eval> [--repeats N] [--split S] [--tag T] [--cases id,id] [--sample N]
                 [--baseline main|latest|<exp>|git:<ref>] [--strict]
                 [--report md,json,junit] [--out DIR] [--variant TEXT] [--no-cache]
operonx eval compare <expA> <expB> [--pairwise <judge>] [--report md]
operonx eval report <exp> [--format md|json|junit|html]
operonx eval rescore <exp> [--evaluators mod:attr,...]   # Inspect's `inspect score`
operonx eval calibrate <eval> [--runs 3]                  # A/A noise floor → tolerance/repeats
operonx eval power <eval> --delta 0.05 [--alpha 0.05 --power 0.8]
operonx eval align <judge> [--queue Q]                    # κ / TPR / TNR vs human scores
operonx eval dataset validate|diff|stats <name>           # schema, dupes, version, git diff by case
operonx eval dataset from-runs <name> --filter origin=service,name=call --sample 50 [--review bad]
operonx eval online run|backfill <rule> [--since 7d]
operonx eval migrate-reviews
```

`operonx run <eval>` keeps working, since the job path is unchanged.

---

## 13. pytest integration (opt-in)

There is no `pytest11` entry point, so installing operonx never changes someone's test run.
Enable it with `pytest_plugins = ["operonx.app.evals.pytest_plugin"]` or `-p
operonx.app.evals.pytest_plugin` (the LangSmith model).

```python
# 1) gate a declared eval once per session
@pytest.mark.operonx_eval("replies", repeats=1, baseline="latest")
def test_replies_hold_up(eval_result):
    assert eval_result.gate.verdict != "regressed", eval_result.report("md")

# 2) one pytest test per case: the dataset as parametrised unit tests
from operonx.app.evals import Dataset
@pytest.mark.parametrize("case", Dataset("datasets/critical.jsonl").cases(), ids=lambda c: c.id)  # cases(): new
async def test_critical(case, run_case):
    got = await run_case("bot:reply_flow", case)        # traced, origin=eval
    assert got.check(exact("intent")), got.why
    assert got.trace.path(types={"llm"}) == ["classify", "answer"]
```

The pytest session is **one experiment** (`variant="pytest"`). Each test becomes an item with its
scores, so a pytest run shows up in Studio with drill-down to traces, and
`--junitxml` works as usual. LLM-hitting tests should carry `@pytest.mark.live` so the default
`pytest` stays offline (the repo already separates `tests/live`).

---

## 14. CI gating

* **Baseline resolution** (`--baseline main`): find the latest *finished*
  experiment of the same eval + `dataset_version` + `evaluators_hash` whose
  `code_version` is `git merge-base HEAD origin/main`, from the score store. If there is none,
  either `--baseline-run` runs it in a temporary `git worktree` at that sha (2×
  cost, explicit) or the gate degrades to absolute thresholds and says so. A
  nightly scheduled eval on `main` keeps baselines warm, so MRs pay for one run.
* **Writing to the team store from CI** needs ClickHouse credentials in CI
  variables. Without them, the report artifacts still work and the experiment can be
  imported later (`operonx eval import <dir>`).
* **Reports:** `report.md` (gate verdict, metric table with CI and diff, top
  regressed cases with output snippets and a Studio link, judge warnings, cost),
  `junit.xml` (one testcase per case×evaluator; GitLab and GitHub render it
  natively), `experiment.json`.

```yaml
# GitLab (this team creates MRs on GitLab)
eval:
  stage: test
  script:
    - uv run operonx eval run replies --baseline main --repeats 3 --report md,junit --out out/eval
  artifacts:
    when: always
    reports: { junit: out/eval/junit.xml }
    paths: [out/eval/]
  rules: [ { if: '$CI_PIPELINE_SOURCE == "merge_request_event"' } ]
# exit 1 regressed/failed → red; 3 infra → retry the job, not "quality failed"
```

```yaml
# GitHub Actions (operonx's own repo)
- run: uv run operonx eval run replies --baseline main --report md,junit --out out/eval
- if: always()
  run: gh pr comment ${{ github.event.pull_request.number }} --body-file out/eval/report.md
```

---

## 15. Studio pages and routes

### 15.1 Pages (all follow the one-hop rule, `docs/REFACTOR_PHASE2.md:346`)

| Page | What | Builds on |
|---|---|---|
| **Evals → Experiments** | Per eval: experiments list (variant, short sha + dirty dot, dataset version, repeats, gate badge), metric cards **with CI**, sparkline with CI band and version markers, cost/latency. | `evals.js:33-147` |
| **Experiment detail** | Gate verdict + reasons; metric table (mean, CI, n, method); slices (exploratory badge); case table: status per repeat (●●○), flip class, per-evaluator chips with reason (existing chips at `evals.js:240-247`), cost, ms; filters All/Failed/Changed/Flaky/Critical. | `evals.js:149-262` |
| **Case drill-down** (side panel) | Input, expected, output per repeat; every score with reason, judge trace link, blame op; **"Open trace"** opens the run view with the blamed op selected and the Errors/Values lens on (`runview.js` lenses); trajectory strip (expected vs actual op path, diffs highlighted); "Add as must-pass", "Re-run ×5", "Edit expected". | `evals.js:253` "Open run" |
| **Compare** | Choose A/B (default: baseline). Comparability banner (dataset/evaluator hash). Per metric: A, B, diff, paired CI, McNemar p, verdict. Flips grouped by class. Per-op cost/latency deltas across all cases (`op_stats(RunFilter(job_run=A))` vs B, which already exists at `runs/base.py:93-97`). Side-by-side output diff per case. Optional pairwise judge + pairwise human queue. | `app.py:1841-1866` (single-run compare), `runview.js:272` |
| **Datasets** | List (version, cases, splits, tags, used_by); case table with inline edit of expected/tags/split, archive; **case history** (this case across the last N experiments: a pass/fail strip, which is how a "known flaky" case is spotted); import from runs/review/CSV; validation errors. | `evals.js:264-295`, `app.py:2782-2815` |
| **Online** | Rules (filter, sample, evaluators, budget/spend), score trend charts per score name with deploy (version) markers, failing targets list → trace / queue. | new; Monitor style |
| **Review → Queues** | Queue list with progress; reviewing extends the existing conversation reader with rubric fields from score configs; keys `g b j k` kept (`review.js:181-183`); pairwise mode. | `review.js`, `review.py` |
| **Judge alignment** | Per judge: κ, TPR, TNR, n, confusion matrix, inconsistency-under-swap rate, disagreements list → "add as example" (writes the rubric file as a diff card, the same path as assistant edits). | new |

### 15.2 Routes (existing kept; new ones in `operonx_studio/evals.py`, registered from `app.py`, which is already 4188 lines)

| Method + route | Status | Purpose |
|---|---|---|
| `GET /api/p/{pid}/evals` | **extend** (`app.py:2821`) | Adds metrics+CI, gate, fingerprint per run, read from the ScoreStore (falls back to job records). |
| `GET /api/p/{pid}/evals/{name}/runs/{run_id}` | keep (`app.py:2844`) | Back-compat; becomes a thin wrapper over the experiment route. |
| `GET /api/p/{pid}/experiments?eval=&dataset=&version=&limit=` | new | Experiment list across hosts (ClickHouse) and local records. |
| `GET /api/p/{pid}/experiments/{id}` | new | Summary + items (repeats) + per-item scores (one hop). |
| `GET /api/p/{pid}/experiments/{id}/cases/{case_id}` | new | Drill-down: repeats, scores, trace ids, judge trace ids, blame ops, trajectory diff. |
| `GET /api/p/{pid}/experiments/compare?a=&b=` | new | Paired stats, flips by class, op deltas, comparability. |
| `POST /api/p/{pid}/experiments/{id}/rescore` | new (edit) | Re-run evaluators on stored traces. |
| `POST /api/p/{pid}/evals/{name}/run` | new (edit) | `{repeats, cases, split, baseline}`; spawns `operonx eval run` the way `app.py:2287` spawns jobs. |
| `GET /api/p/{pid}/datasets` | new (split out of `/evals`) | List + versions. |
| `GET /api/p/{pid}/datasets/{name}` / `POST …/rows` | keep (`app.py:2895,2915`) | Existing behaviour. |
| `PATCH /api/p/{pid}/datasets/{name}/rows/{case_id}` | new (edit) | Edit expected/tags/split/status; atomic rewrite via operonx `Dataset.update`. |
| `GET /api/p/{pid}/datasets/{name}/cases/{case_id}/history` | new | Case across experiments. |
| `GET /api/p/{pid}/scores?trace=&experiment=&name=` | new | Scores for any target (the run view shows a trace's scores too). |
| `GET /api/p/{pid}/scores/series?name=&origin=&since=&bucket=` | new | Trends. |
| `GET/POST /api/p/{pid}/online/rules` | new | Read / write `[[online_eval]]` (via `yamledit`/toml edit like Settings). |
| `GET /api/p/{pid}/queues`, `POST /api/p/{pid}/queues`, `GET …/queues/{q}/next` | new | Queues. |
| `POST /api/p/{pid}/review/run/{run}` | **extend** (`app.py:2456`) | Writes human score rows (plus rubric fields); same request body accepted. |
| `POST /api/p/{pid}/review/run/{run}/dataset` | **extend** (`app.py:2471`) | `expected`, `split`, `op_id` options. |
| `GET /api/p/{pid}/evaluators/{name}/alignment` | new | κ/TPR/TNR/confusion/disagreements. |

Permissions follow `docs/TEAM_PLAN.md:126-133,410`: viewers read, editors run, review
and change datasets. The "reviewer" level that D11 deferred becomes worth adding here
(review and annotate only).

Assistant tools (`mcp.py:245`): `run_eval` gains `repeats` and `baseline` and reports the
gate verdict with CI. New `compare_experiments`, `open_case`, `explain_failure`
(reads the case's trace and scores and proposes a fix as a diff card).

---

## 16. Package placement

| Component | Place | Why |
|---|---|---|
| Dataset, Eval (experiment), Gate, evaluator protocol, built-ins, TraceView, stats, compare, rescore, OnlineEval, RunStoreSource, pytest plugin | **core** `operonx/app/evals/` (package; `__init__` re-exports the 1.9.0 names so `from operonx.app.evals import Eval, contains, llm_judge` still works) | Evals are a Job and need trace internals. Already in core since 1.9.0. No new dependencies. |
| Judge graphs | `operonx/app/evals/judges.py` | They need `providers.ops.LLMOp`, which is already a core module (provider SDKs remain extras). |
| `ScoreStore` + backends (files/sqlite/clickhouse) | **core** `operonx/telemetry/scores/` | Same layer and same backends as `telemetry/runs`. The ClickHouse backend uses the existing `clickhouse` extra. |
| CLI | `operonx/cli/eval.py` (delegated) | One command, one parser per subcommand (`cli/main.py:1-15`). |
| Guide | `operonx/guide/` + `tests/guide/` snippets, `docs/guide/13-evals.md` rewrite | Guide snippets are CI-tested (repo rule in `CLAUDE.md`). |
| UI | **operonx-studio** only (`operonx_studio/evals.py`, `static/evals.js`, `static/datasets.js`, `static/online.js`, `static/review.js`) | Studio renders and calls operonx APIs, "never a copy of its rules" (`app.py:2780`). |

Not a separate `operonx-evals` package. Its version would be lock-stepped with job and
trace internals anyway, and that split would only add an install step and a compatibility
matrix.

---

## 17. Must / Later / Avoid

**Must**
1. Fingerprint on every experiment (code, graph, config, dataset, evaluators, operonx).
2. `repeats` with flakiness classes and pass^k.
3. Statistics: Wilson/CLT/clustered CI, paired bootstrap, McNemar, three-state gate, must-pass tier, infra exit code.
4. `TraceView` (live == stored), trajectory/tool/op-output/budget evaluators, `rescore`.
5. `ScoreStore` (files+SQLite, ClickHouse v3) for experiments, items and scores; idempotent ids.
6. Judge as a traced graph, with versioning, cache, binary default, position-swap pairwise, judge cost split out.
7. `operonx eval` CLI (run/compare/report/rescore/calibrate/power/list), md/json/junit reports.
8. Opt-in pytest plugin.
9. Studio: experiments with CI, compare, case drill-down to blamed op, dataset editor + case history.
10. Online eval (sampled, scheduled, budgeted, backfill) + score alerts + trends.
11. Reviews become human scores; queues with rubric; judge alignment (κ/TPR/TNR).

**Later**
- Multi-turn `turns` cases + simulated-user cases (callbot QC port) - the first Later item, since callbot is the main consumer.
- Judge panels (majority) + Krippendorff's α; bias-corrected pass rates from judge TPR/TNR.
- Power-analysis-driven dataset sizing in Studio; dataset splits UI with git history per case.
- Langfuse sync (scores to Langfuse traces; datasets export); OTel `gen_ai.evaluation.result` export once the conventions stabilise.
- Hill-climbing mode (baseline output as `expected`, Braintrust `BaseExperiment`).
- Assistant-generated evaluators/criteria from reviewed failures (EvalGen-style, always human-aligned).
- Distributed eval workers (only if one host's concurrency is measured to be the bottleneck).

**Avoid**
- A second runtime or scheduler for evals (Job already does this).
- Datasets that live only in a database.
- 1–10 Likert judge scales as the default; judges checking several criteria at once.
- Bundling a metric zoo (Ragas/BLEU/BERTScore) in core; keep them as recipes.
- Running evaluators inline in services.
- Gating on every slice; reporting a diff without a CI.
- An auto-loaded `pytest11` plugin.
- A query language over scores (the filter-object rule, `runs/model.py:378-386`).
- Vendor SDK dependencies (LangSmith/Braintrust/DeepEval) in core.

---

## 18. Phased roadmap with tests

The process follows the repo's habits: plan doc first (`docs/EVALS_PLAN.md` in Operon,
committed), one branch per phase, evidence before each step, and guide snippets tested.
Callbot migration is a follow-up on `refactor/operonx-studio` (operonx is upstream).

| Phase | Deliverable | Tests (all new unless noted) | Measurement gate before moving on |
|---|---|---|---|
| **P0: Baseline & plan** | `docs/EVALS_PLAN.md`; callbot QC cases run as an `Eval` on a scratch branch | none in the repo | Run the QC eval 3× on one sha and record per-case flip rate and pass-rate SD. These numbers set the default `repeats`/`tolerance`. |
| **P1: Identity, repeats, stats** | fingerprint; `repeats`; `stats.py` (Wilson, clustered SE, paired bootstrap, McNemar, pass^k, Holm/BH); `Gate` three-state; exit codes; `evals.py` → package with identical exports | `tests/internal/app/evals/test_fingerprint.py` (same code → same hash; prompt param change → `graph_hash` changes; secrets not in `config_hash`; dirty flag), `test_repeats.py` (N items per case, flaky classes), `test_stats.py` (Wilson 45/50 → [0.786, 0.957]; McNemar b=8,c=1 → 0.0391; pass^3 with 4/5 → 0.4; seeded bootstrap deterministic; clustered SE ≥ naive under intra-cluster correlation), `test_gate.py` (**A/A simulation: 1000 synthetic paired runs with no true effect → REGRESSED ≤ 5%**; a true −10 pt drop on 300 cases → REGRESSED ≥ 80%); existing `tests/internal/app/test_evals.py` unchanged and green | All 11 existing eval tests pass unmodified; judge-free eval overhead per case is measured before and after (target: no measurable change at `repeats=1`). |
| **P2: TraceView & trajectory** | runner passes the live trace; `TraceView`; trajectory/tool/op_output/budget; concurrent evaluators; `rescore` | `test_traceview.py` (**golden: `from_trace(live)` == `from_rows(store.get_run(id).nodes)` for files and sqlite stores** on a graph with a subgraph, a branch and an LLM fake); `test_trajectory.py` (table tests of the 4 match modes matching AgentEvals semantics; tool-call args exact/subset); `test_rescore.py` (deterministic evaluators reproduce identical verdicts; no graph run happens) | Evaluators that don't request `trace` add ~0 ms (benchmark). Concurrent judging cuts wall time on a 50-case fake-judge eval (measured). |
| **P3: ScoreStore** | `telemetry/scores` contract; files+SQLite; ClickHouse schema v3 migration; background writer; Studio reads with fallback to records | contract suite parametrised over backends (like the run-store tests); `test_idempotent_ids.py` (re-put collapses); migration test v2→v3 on an existing DB; `tests/live/test_clickhouse_scores.py` against the team ClickHouse | An experiment run on host A is visible on host B through ClickHouse; a ClickHouse outage during an eval loses no verdicts (the record is still written). |
| **P4: CLI, reports, pytest, CI** | `operonx eval …`; md/json/junit; pytest plugin; baseline resolution; guide pages + CI examples | `tests/internal/cli/test_eval_cli.py` (exit code matrix 0/1/2/3), junit XML schema check, `pytester` tests for the plugin (session = experiment; parametrised cases), `tests/guide/` snippets | A real MR pipeline (operonx repo) shows the junit widget and the md report. |
| **P5: Judges** | judge graph traced; versioning; cache; pairwise with swap; alignment computation | fake-LLM tests: swap-inconsistent → `tie` + `inconsistent`; cache hit → zero LLM calls; judge trace carries `role=judge, judged_trace`; κ/TPR/TNR against hand-computed fixtures; unvalidated-judge warning appears in the report | On a 100-item human-labelled set (from callbot reviews), measure κ for the polite/handoff judges. The numbers are recorded, not assumed. |
| **P6: Studio** | experiments, detail, drill-down, compare, datasets editor, case history; extended `/evals` | `tests/studio/test_experiments.py` (API shapes, one-hop payloads, compare stats wiring, dataset PATCH atomicity), existing `tests/studio/test_evals.py` green; desktop + phone screenshots per change (team UI rule) | Every new screen opens in 1 hop; through-tunnel load time measured as in `REFACTOR_PHASE2.md:701`. |
| **P7: Online + queues + alerts** | `RunStoreSource`, `OnlineEval`, budgets, backfill; queues, rubric score configs; reviews → scores migration; score alerts; trends page | `test_online.py` (stable sampling: same trace → same decision across runs; cursor resume; budget stops; reference-needing evaluator refused); `test_queues.py`; `test_alerts_scores.py`; studio review back-compat (old `reviews.jsonl` reads as scores) | One week of callbot staging traces backfilled at 5%: judge spend per day measured against budget; the latency of the service is unaffected (it's off-path, verified with the existing call metrics). |
| **P8 (Later): Conversations** | `turns` cases, simulated-user cases, session targets, QC port in callbot | callbot `tests/qc` ported; scripted 3-turn case through doors | QC pass rates match the old runbook on the same sha (A/A between old and new harness). |

---

## 19. Resolved choices (so nothing is left open by default)

| Question | Resolution | Reason |
|---|---|---|
| Where do datasets live? | Git JSONL; DB never sole copy | Reviewable, diffable, already how Studio writes them (`app.py:2499,2968` via operonx `Dataset.add`). |
| One store or two? | Separate `ScoreStore` contract, same backends/DB | Keeps `RunStore` at five methods (`runs/base.py:3-7`). |
| Default judge scale | Binary PASS/FAIL | Calibratable; κ/TPR/TNR meaningful. |
| Default gate | Absolute threshold if given (1.9.0 semantics) + baseline comparison when `gate.baseline` set | Zero behaviour change for existing users. |
| Inconclusive in CI | Exit 0 with warning; `--strict` → 2 | Small datasets would otherwise block every MR on noise. |
| Online eval inline? | Never | Callbot latency; run store already decouples writes. |
| Separate package? | No | §16. |
| pytest plugin auto-load? | No, opt-in | Don't alter users' test runs. |
| Score retention | Eval/human forever; online 365 d with snapshot | Outlive 30-day service traces. |

The only choices that belong to the team are operational, not design: (a) giving CI the
ClickHouse credentials it needs to write experiments, and (b) the daily judge budget for online eval on production.

---

## 20. References

- Langfuse experiments data model: https://langfuse.com/docs/evaluation/experiments/data-model
- Langfuse core concepts (scores, sources, targets, annotation queues): https://langfuse.com/docs/evaluation/core-concepts
- Langfuse online evaluation: https://langfuse.com/docs/evaluation/get-started/online.md
- LangSmith evaluation concepts: https://docs.langchain.com/langsmith/evaluation-concepts
- LangSmith pairwise: https://docs.smith.langchain.com/evaluation/how_to_guides/evaluate_pairwise
- LangSmith pytest: https://docs.langchain.com/langsmith/pytest
- LangSmith trajectory evals / AgentEvals: https://docs.langchain.com/langsmith/trajectory-evals
- Braintrust writing evals: https://braintrust.dev/docs/guides/evals/write
- Braintrust analysing results: https://www.braintrust.dev/foundations/how-to-analyze-your-eval-results
- Arize Phoenix evals: https://arize.com/docs/ax/integrations/evaluation-integrations/phoenix-evals
- Inspect AI scorers: https://inspect.aisi.org.uk/scorers.html ; full docs (epochs reducers, multi-grader, Krippendorff α): https://inspect.aisi.org.uk/llms-full.txt
- OpenAI graders: https://developers.openai.com/api/docs/guides/graders
- promptfoo in CI: https://medium.com/@alexrodriguesj/testing-llm-prompts-like-code-regression-evals-in-ci-cd-with-promptfoo-5242b4dcb9be
- DeepEval: https://github.com/confident-ai/deepeval
- Ragas metrics (via MLflow): https://mlflow.org/docs/latest/genai/eval-monitor/scorers/third-party/ragas/
- τ-bench (pass^k): https://arxiv.org/pdf/2406.12045
- Anthropic, statistical approach to evals: https://www.anthropic.com/research/statistical-approach-to-model-evals ; paper https://arxiv.org/pdf/2411.00640
- Zheng et al., Judging LLM-as-a-Judge (MT-Bench): https://arxiv.org/pdf/2306.05685
- Shankar et al., Who Validates the Validators (EvalGen, criteria drift): https://arxiv.org/pdf/2404.12272
- Judge bias mitigation study (2026): https://arxiv.org/pdf/2604.23178
- Judge calibration with Cohen's κ: https://levelup.gitconnected.com/llm-as-a-judge-calibration-cohens-kappa-and-judge-bias-in-production-e8e7b58ba064
- Husain evals skills (binary judges, TPR/TNR): https://skills.sh/hamelsmu/evals-skills/eval-audit
- OpenTelemetry GenAI conventions overview: https://www.truefoundry.com/blog/opentelemetry-genai-semantic-conventions
