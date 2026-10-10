# Doorless services: serve a graph by its signature

**Status (2026-10-10): PLANNED.** Nothing implemented yet.

**Asked for:** understand ingress/egress, the Application, services and jobs, and reduce the design
where it can be reduced. The walk-through page with real samples:
https://claude.ai/artifact/LqZVGqQrNZW2EM9PmDkXoR

**The rule this plan adopts:** a graph's parameters are what the caller sends; its outputs are what
the caller gets back. `ingress`/`egress` are for runs that handle many items (a call, a stream).

## 1. What is there today

Read from operonx 1.18.1.

- **One funnel.** Every run a service or a job starts goes through `serve_session(engine, session,
  request)` (`app/serve/runner.py`). It puts the session on the run's scratch and calls
  `engine.start(inputs)`. `ingress` reads the session's items (`current_session().recv()`); `egress`
  sends to it.
- **A Job uses the same funnel.** `jobs/runner.py::_attempt` builds a `JobSession` and calls
  `serve_session`. It accepts **two graph shapes** (`Job.has_doors()`):
  - no doors: the item is bound to the graph's parameters (`Job.bind`), and the run's outputs are
    the item's result (`_plain(await handle.result())`);
  - doors: the item is fed to `ingress`, and what `egress` sends is the result.
- **A Service accepts only the door shape.**
  - Run inputs: with no `on_session`, the query string becomes the inputs
    (`_default_on_session`, `serve/app.py`). This half already works for a doorless graph.
  - Reply: the HTTP endpoint answers with `session.reply`, which only `egress` fills. A doorless
    graph produces no reply, so the caller gets `500 "the graph produced no output"`.
- **A schedule can only start a graph.** A scheduled sweep over many items has to loop by hand with
  `invoke` inside one op: no per-item record, no resume, no concurrency.
- **Services can be declared twice over.** `Service(...)` in Python and `[[serve]]` in
  `operonx.toml` both build the same `ServeSpec`. Jobs and evals exist only in Python.

## 2. What it costs in the projects

Every project that uses operonx was checked (2026-10-10).

| Project | Uses | Doorless serving (S1) | Scheduled job (S2) | No `[[serve]]` (S3) |
|---|---|---|---|---|
| **mr-finance** | 8 http services, a 07:00 schedule, 3 jobs, 1 eval | removes 9 wrapper graphs and 8 `read_request` ops | replaces `sweep_flow` + `sweep_all`, the second copy of the `policy_sweep` loop | already Python |
| **meeting-prep-operonx** | `mail` webhook, `approve` GET, `morning` schedule, `golden` eval | `approve(draft, decision)` already takes its query as parameters and keeps a wrapper only to call `egress`; `sweep` calls `ingress()` only to accept a tick it never reads; `on_mail` and `golden_case` lose their wrappers | optional | already Python |
| **tcb-wepro** | 1 http service | `chat_flow` goes from 5 ops to 2 | — | already Python |
| **educa-reminder-agent** `refactor/operonx-studio` | the `call` websocket with its own door ops; `call_summary` http; `qc`, `backfill` jobs | **the call is untouched** (a stream keeps its doors); `call_summary_api` may drop from 3 ops to 1 | — (jobs run from cron) | already Python |
| **educa-reminder-callbot** `feat/serve-migration` | the same call, in `[[serve]]` | untouched | — | **needs migration**: `call` and `admin` into `app/main.py` |
| **qc-snatcher** | 7 jobs, no services | — | — | no `[[serve]]`; it uses `[[graph]]`, which stays |
| **educa-reminder-agent** `staging` | no operonx | — | — | — |

**Nothing breaks under S1 or S2.** Both add a shape; graphs with doors behave exactly as today. Only
S3 removes something, and only educa-reminder-callbot uses it.

## 3. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| **D1** | Can a graph without doors be served? | **Yes**, on the one-shot doors: `http`, `webhook`, `schedule`. A stream door (`websocket`, any `STREAM_KINDS`) still requires doors: refused at declaration with a message that says so. | A stream needs items over the run's lifetime; a request does not. |
| **D2** | How is "has doors" decided? | One function, `has_doors(graph)`, moved out of `Job.has_doors()` into `operonx/app/doors.py`. A graph with any `@op(door="ingress")` op anywhere has doors. Jobs and services both call it. | One rule for both. A project's own door op (the callbot's `receive_audio`, `op.door == "ingress"`) counts too. Today `Job.has_doors()` looks only for the library `ingress`, so a Job over a graph with its own door op binds the item to parameters instead of feeding it; S1 fixes that and tests it. |
| **D3** | How does the body reach the parameters? | `bind_item(params, item, fixed, input=None)`, moved out of `Job.bind` into `operonx/app/doors.py`, the same rules: a dict fills parameters by name; anything else goes to the only free parameter; an unknown field is refused. | Same behaviour as a Job item; no second set of rules. |
| **D4** | Query string and body together | The query fills parameters as today. The body fills the rest. A name in both is refused (`400`, naming it). A parameter with neither, and no default, is refused (`400`). | An explicit refusal beats a silent override. |
| **D5** | When is a bad body refused? | **Before the run.** The http and webhook endpoints call `bind_item` against the compiled graph's parameters right after decoding, like the codec check today, and answer `400 {"error", "endpoint", "field"}`. No run is minted. | A webhook must say no before its `202`; an http caller gets a 400, not a 500 with a trace. |
| **D6** | What is the reply? | What `Operon.run` returns for the same graph and inputs, without the `$` keys (`_plain(await handle.result())`), the same rule as a doorless Job item and as `invoke`. Sent through `session.send`, so the http endpoint, SSE streaming and the webhook callback all work unchanged. | One meaning of "a graph's result" across run, invoke, job and service. |
| **D7** | Reply shape | The outputs dict as it is. No unwrapping of a single key. | Predictable. A graph that wants a flat body has its last op return the fields at top level. |
| **D8** | A doorless run that fails | Unchanged: an unhandled op error leaves no reply, and the endpoint answers `500` with the trace id (`_no_output`). Nothing is sent. | The existing rule; a failure never answers 200. |
| **D9** | A schedule's tick on a doorless graph | `{"tick", "at"}` fills parameters named `tick` and `at` if the graph has them; otherwise it is dropped, never refused. | A clock's item is metadata, not a caller's data. A graph with no parameters is the common case. |
| **D10** | `on_session` with a doorless graph | Allowed. The hook's inputs plus the body must together match the graph's parameters (`_inputs_fit` checks the union). | Keeps hooks for lookups and refusals. |
| **D11** | A graph that has doors **and** is doorless-served? | Not a case: with doors, nothing changes (the item goes to `ingress`, `egress` is the reply). | Backward compatibility is the default, not a flag. |
| **D12** | Where does a scheduled job live? | `Job(..., schedule=schedule(at="07:00", port=PORT))`, reusing the `schedule()` listener so the port and clock are explicit. `operonx serve` runs the job's clock beside the port's services; `operonx run <job>` still runs it on demand. | The job says what, the schedule says when; no wrapper graph. |
| **D13** | A tick while the job is still running | Skipped and counted, as `ScheduleTransport` does for graphs. Each tick is a fresh job run (no resume), tagged `trigger=schedule` and the slot. With `queue=`, a tick fires on one replica only, as today. | The existing schedule rules, applied to a job. |
| **D14** | `[[serve]]` in `operonx.toml` | Deprecated in 1.19: a `DeprecationWarning` naming the file and pointing to `Service(...)`. Removed in 2.0. `[project]`, `[resources]`, `[tracing]`, `[studio]` and `[[graph]]` stay. | One place to declare services; removal is breaking, so it waits for a major. |
| **D15** | Templates and guide | `operonx init --template http` and `chat` generate doorless graphs. The guide's serving page leads with the doorless shape and keeps doors under "streams". `agent` follows whatever S1 lands with. | New projects should start with the short form. |
| **D16** | Webhook as an http option (`wait=False`) | **Not done.** | The webhook also carries the durable queue and `multitask=`; renaming saves nothing real. |

## 4. Phases

### S1: operonx — doorless one-shot doors (1.19.0)

1. `operonx/app/doors.py`: `has_doors(graph)` and `bind_item(...)`, moved from `Job`; `Job` calls
   them. Job tests pass unchanged.
2. `ServeRunner._run_one`: when the engine is doorless, take the session's one item, merge it into
   `request.inputs` by D3, D4, D9 and D10, run, then `session.send(_plain(await handle.result()))`
   when the run did not fail.
3. Endpoints (`_http_endpoint`, `_webhook_endpoint`): validate the body with `bind_item` before the
   run (D5). The engine's parameters come from the compiled graph (`inputs_expected`).
4. `Service(...)`: refuse a doorless graph on a stream listener (D1).
5. `describe_service`: `"doors": true|false`, so `--list` and the studio can show which shape a
   service has.
6. Guide page and tested snippet (`operonx/guide`, `tests/guide`); templates (D15); CHANGELOG.

**Gate G1 (measured, not assumed):**
- A doorless `chat_flow(question)` served on `http` answers `200` with its outputs, in a TestClient
  test.
- Refusals: an unknown body field → `400` naming it; a missing required parameter → `400`; a name
  in both query and body → `400`; none of the three mints a run (no trace written).
- A failing op → `500` with the trace id, as today.
- Webhook: the same refusals before the `202`; a doorless webhook's outputs reach `?callback=`.
- SSE: a doorless graph answered as a stream sends exactly one event, its outputs.
- Schedule: a doorless graph with no parameters runs on each tick.
- The whole suite passes with no change to any door graph's test.
- **Latency:** 1,000 requests through the TestClient to a trivial graph in both shapes (door wrapper
  vs doorless, same work). The doorless p50 must not be slower than the door shape's p50. Script and
  numbers go in this doc.

### S2: operonx — a schedule starts a Job (1.19.0)

1. `Job(schedule=...)` accepts a `schedule()` listener; `describe_job` shows it.
2. The serve layer starts one clock per scheduled job on that port. Each tick calls `job.run()`;
   skip-if-running and `queue=` single-replica firing by D13. Records go where the job's records
   always go.
3. Guide snippet; CHANGELOG.

**Gate G2:**
- A test with a fast clock (`every=1`): ticks produce job runs with per-item records.
- A tick that lands during a slow run is skipped, and `skipped` counts it.
- A failing job run does not stop the clock.

### S3: operonx — deprecate `[[serve]]` (1.19.0, removal in 2.0)

1. `Manifest` warns once per process when it parses `[[serve]]`, naming the file and the
   replacement.
2. The guide stops showing `[[serve]]`; the manifest reference keeps it, marked deprecated.

**Gate G3:** loading a manifest with `[[serve]]` warns exactly once; one with only `[project]`,
`[[graph]]` and `[tracing]` does not warn.

### S4: the projects (each in its own repo, one PR each)

| Project | Change | Gate |
|---|---|---|
| tcb-wepro | `chat_flow(question)`, doorless; `read_question` goes; the reply op returns `{answer, error}` at top level | its tests pass; `POST /chat` answers as before |
| mr-finance | the 8 `*_api` wrappers and `read_request` ops go; validation moves to parameter defaults or a first op; `morning_sweep` becomes `Job("policy_sweep", ..., schedule=schedule(at="07:00"))`; `sweep_flow` and `sweep_all` go | its 61 tests and `tests/test_demo.py` pass; the web UI works (desktop and phone screenshots) |
| meeting-prep-operonx | `approve`, `on_mail`, `sweep` and `golden_case` lose their door wrappers | its tests and the `golden` eval gate pass |
| educa-reminder-agent `refactor/operonx-studio` | optional: `call_summary_api` doorless. The call is not touched. Work stays on that branch, never toward staging. | its tests pass; `POST /api/v1/calls/summary/batch` answers the same JSON |
| educa-reminder-callbot | `[[serve]]` → `app/main.py`, if the repo is still in use (open question 1) | its tests pass with no deprecation warning |
| qc-snatcher | nothing | — |

### S5: studio check

A doorless served graph has no door ops to draw as entry and exit. The studio draws the service's
entry from the graph's parameters and its exit from the graph's outputs, and the http playground
still drives the door (it goes through the same transport).

**Gate G5:** the tcb-wepro and mr-finance services open in the studio with entry and exit shown, and
one playground request each succeeds.

## 5. Open question

1. **Is educa-reminder-callbot still in use?** The same callbot lives on educa-reminder-agent's
   `refactor/operonx-studio` branch, declared in Python. If the standalone repo is retired, S3 needs
   no migration at all; if not, its `[[serve]]` moves to `app/main.py` before 2.0.
