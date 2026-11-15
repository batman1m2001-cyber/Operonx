# Cost plan: an operonx project must cost less than an empty folder

**Why.** operonx exists to make building cheaper by reusing parts. E7 showed the opposite. On the same
seven tasks, a project made by `operonx init` cost **2.3×** an empty folder ($1.75 against $0.65 per
track, [engineering_env/E7.md](engineering_env/E7.md)). This plan finds where that money went and
removes it.

**Gate.** Re-run the E7 harness (`bench/token_ab`) with the lean project:

1. **Arm A' ≤ arm B.** The lean arm passes 14/14 sessions and costs no more than the existing
   arm B rows.
2. **Arm A' clearly cheaper where reuse matters.** On new tasks that need tracing, streaming or a
   scheduled job with retries, A' beats B.

Spend cap: $15.

## 1. Where the money went (measured on the E7 session logs)

Every turn resends the whole context: the system prompt, AGENTS.md, and everything read so far. So
cost ≈ context size × number of turns.

| | arm A (operonx init) | arm B (empty folder) |
|---|---|---|
| turns, 2 tracks | 175 | 93 |
| tool calls | 160 | ~80 |
| context on turn 1 | 24.8k tokens | 19.0k tokens |
| share of cost: prompt × turns | 67% | 80% |
| share of cost: reading the guide and the BRD | 21% | 0% |
| share of cost: writing code | 7% | 5% |

Where arm A's extra turns came from:

- **Learning operonx again every session: ~25 calls.** AGENTS.md rule 2 says "before you use an
  operonx API, re-read the guide page that covers it", so every session re-reads `01-ops.md`,
  `02-composition.md`, `05-project-layout.md`, `06-failures.md`. That is 5–6k tokens per read, and the
  same pages are read again in the next task.
- **Verifying through a ritual: 30 pytest + 11 uv + 18 serve calls.** Arm B verified with 38 serve
  and curl calls and no tests.
- **Reading a layered feature: 66 calls.** One change touches routes, ops, graph, `_store` and a test.
  Each call is `cat` over 5–8 files.

Reuse already works. Example from T5a: making the model calls survive one 503 took arm A **one line**
(`retry: {max_attempts: 3, …}` in `resources.yaml`). Arm B wrote about 20 lines of retry code. Arm A
still paid more, because of the learning and the ritual around that line, not the line itself.

## 2. The levers, in order of the money they remove

| # | lever | removes | change |
|---|---|---|---|
| K1 | **Recipes, not a guide** | the 21% learning cost and ~25 turns | AGENTS.md carries a ≤1.5k-token cheat sheet: the exact snippet for an op, an LLM call, a route, a job, a retry, a page. Rule 2 becomes "use the cheat sheet; open a guide page only for what it does not cover". |
| K2 | **`operonx add`: reuse as one command** | ~8 turns per new feature | `operonx add route tickets POST /tickets`, `add job daily_summary`, `add llm classify`, `add page stats`. Each writes the wired code and prints the 10 lines the agent fills in. This turns reuse into a single command. |
| K3 | **`operonx check`: one verify call** | ~40 turns (pytest, uv, serve, curl loops) | lint, tests if any, boot the service, hit every route once, then print a ≤20-line pass/fail. AGENTS.md says to run this one command. |
| K4 | **One file per feature** | ~half the code-reading calls | Ops, graph and routes stay in `src/<feature>.py` until the file passes ~300 lines. The layered folder becomes opt-in. |
| K5 | **A small AGENTS.md** | the 5.8k extra tokens on every turn | AGENTS.md goes from 7.3 KB to ≤2.5 KB. BRD, board, CHANGELOG and knowledge rules move to `operonx init --brd`. `.operonx/runs` goes in `.gitignore`. Tests are written when a task asks for them or a bug was fixed. |

K5 needs one more check: AGENTS.md is only 1.8k of the 5.8k extra. Find the rest from the
first-turn context of an A session before cutting. (Checked: it was ~3.0k, not 1.8k. A probe turn
in the same folder read 37,844 tokens with the old AGENTS.md, 34,818 with it empty, 34,821 in
arm B. The rest of the 5.8k was the extra turns' cache writes, not the first turn.)

## 3. Order and measurement

1. **K5 + K1 together, then a pilot.** Run 2 tracks × T1–T3 of arm A', about $1. We expect the
   turns to drop toward arm B's.
2. **K3, then K2.** Both are CLI work in `operonx/cli/` with tests. Run the full A' (2 tracks, about
   $2) against the stored arm B rows. **This is gate 1.**
3. **K4.** The template and lint change. Re-run A' only if K4 changed the template.
4. **E7b.** Three tasks where reuse should win: trace a slow request, stream the summary, a
   scheduled job with retry and resume. Run A' and B, 2 tracks each, about $6. **This is gate 2.**

If gate 1 fails after K1–K5, the project environment does not pay for itself on small products. In
that case, `operonx init` defaults to the lean form and the layered form stays opt-in.

## K5 + K1 result

Done 2026-11-15, board C33. Before/after tree, flow and code changes:
[cost/K5_K1.md](cost/K5_K1.md).

- **Shipped.** AGENTS.md has "How to work here", then Recipes: code that runs for an op/graph, a
  model call, a branch, retry, a job, a route and a Starlette route, a test with the fake model,
  and a failed or slow run. Each recipe ends with `Guide: page § section`, and a test checks that
  every reference names a real page and heading. `docs/brd/` and the board ritual come only with
  `operonx init --brd`.
- **Deviation from K5's size target.** The plan said ≤2.5 KB. The owner said not to write it too
  lean, and to keep careful references so an agent knows where to search for more. AGENTS.md is
  5.4 KB (jobs only) to 6.9 KB (`--http --web`), plus the 0.4 KB guide block. The money came from
  the turns and the doc reads, not from the file's size, so the pilot judges it.
- **Pilot, 2 tracks × T1–T3 of arm A', $1.21:**

  | arm | cost | turns | doc tokens | passed |
  |---|---|---|---|---|
  | A | $2.20 | 94 | ~58.5k | 6/6 (1 fix) |
  | A' | $1.21 | 67 | 4.9k | 6/6 |
  | B | $0.56 | 30 | — | 6/6 |

  This is −45% cost against A, but still 2.2× B. T2 and T3 match B. T1 (a new service) takes
  16–25 turns against B's ~10. In T1, both tracks lost turns to the `asgi("/tickets")` prefix
  (a 405, then reading operonx source), so the route recipe now puts full-path routes in one
  Starlette at `/`. That fix was run in a generated project, not re-piloted. The rest is
  building and checking a service, which goes to K3 and K2.

## K3 result

Done 2026-11-15, board C34. Before/after flow and code changes: [cost/K3.md](cost/K3.md).

- **Shipped.** `operonx check [DIR] [--call] [--no-tests]`: lint (baseline held), the tests when
  there are any, every graph built offline, then every route of every listener resolved through
  that listener's own Starlette router. A route another app answers fails, and the line names what
  answers it and the fix. `GET` routes are called. `--call` calls each `POST`/`PUT`/`PATCH` once,
  with the fields the door asks for and every `llm:` key swapped for the fake model in-process.
  One line per check, a verdict, exit 1 on a failure. 5.6 s on a fresh `--http --web` project,
  tests included. AGENTS.md's step 3 and Commands name it.
- **Scope added: http doors take path parameters and `"$status"`.** The first run of `check` on
  the K5 test project failed lint C14 on the K5 route recipe: it taught a hand-written Starlette
  route for `201`/`404`/`GET /x/{id}`, and C14 (every endpoint is a `Service`) refuses those. The
  bench tasks need all three, and http doors could do none. Rather than weaken C14, the door
  gained them: `http("GET", "/tickets/{id}")` passes `id`, and an op output `"$status": 404`
  sets the code. The route recipe is a `Service` again. The K5 "full paths in one Starlette at
  `/`" text is gone. What stays from it is "list the web mount last", which `check` also catches.
- **Not measured yet.** Gate 1 (the full A' run, about $2) comes after K2.

## K2 result

Done 2026-11-15, board C35. Before/after flow and code changes: [cost/K2.md](cost/K2.md).

- **Shipped.** `operonx add route NAME METHOD PATH`, `add job NAME`, `add llm NAME`, `add page
  NAME`. `route` and `job` write an op and its graph into the feature's `ops.py` / `graph.py` and
  a `Service` / `Job` into `routes.py` / `jobs.py`, then wire the feature's list into `APP` ahead
  of a mount at `/`. The command prints only its own `TODO` lines, and the next command:
  `operonx check --call`. The code runs as written (the op answers its inputs), so the check
  passes before anything is filled in. A taken name, or a route in a project with no port, is
  exit 2 with nothing written.
- **Beyond the plan's four lines, each forced by the bench tasks:**
  - `--llm` on `route` and `job`. T1–T3 are all "a model call, then an op". A model job with no
    inputs (T2's `daily_summary`) gets an `<name>_input` op in front that gathers what the model
    reads.
  - `--in a,b` for body fields; path `{params}` become inputs by themselves (K3's door change).
  - `--feature F`, so T2's job and route share one package (lint C13 refuses reaching across).
  - A model call writes `tests/conftest.py` with the fake model. The first run of `check` after
    `add route --llm` failed the hello template's own route test: `llm:assistant` now needed
    `LLM_API_KEY`. The conftest sets a placeholder and installs the fake. Shown to hold through
    `Application.find` (the reply was `Echo: hi`).
- **K4 hook.** `_feature_files()` is the one mapping from a part (`ops`, `graph`, `routes`,
  `jobs`) to its file. K4's one-file-per-feature shape changes that function, not the generators.
- **Not measured yet.** Gate 1 (the full A' run) is next.

## Gate 1 result

Run 2026-11-15 on main `fc207ae` (K5+K1, K3, K2; before K4), 2 tracks × T1–T6 of arm A', $3.41.
Rows: [bench/token_ab/results/2026-11-15-gate1.jsonl](../bench/token_ab/results/2026-11-15-gate1.jsonl).

**Gate 1 failed.** A' passed 14/14, but cost 2.6× arm B and about the same as the old arm A:

| task | A' cost | A' turns | B cost | B turns | A cost | A turns |
|---|---|---|---|---|---|---|
| T1 new service | $1.31 | 62 (2 fixes) | $0.21 | 11 | $1.34 | 46 |
| T2 job + route | $0.36 | 23 | $0.17 | 9 | $0.52 | 32 |
| T3 report job | $0.33 | 21 | $0.18 | 10 | $0.35 | 16 |
| T4 | $0.28 | 18 | $0.17 | 14 | $0.28 | 12 |
| T5a | $0.37 | 34 | $0.20 | 21 | $0.32 | 25 |
| T5b | $0.47 | 39 | $0.23 | 19 | $0.37 | 26 |
| T6 | $0.29 | 23 | $0.15 | 9 | $0.33 | 18 |
| **all** | **$3.41** | 220 | **$1.31** | 93 | $3.50 | 175 |

Doc reads fell from ~101k tokens (A) to 18k. The recipes work. The money moved to building and
debugging. From the session logs, the causes in order of cost:

1. **An http door could not answer a bare JSON array: $0.90, 39 turns.** SPEC's `GET /tickets`
   answers the list. A graph's reply is a dict, so both tracks answered `{"tickets": [...]}` and
   failed acceptance. Both fix sessions first grepped the guide for `"$body"`, found nothing, then
   read `operonx/app/serve/*.py` to wire `egress` by hand. **Fixed in the K4 PR:** an op that
   outputs `"$body"` answers that value alone, with its `$status` (recipe and guide 02 say so).
2. **A clock listened on 8000 whatever `$PORT` said.** `operonx add job --at` wrote
   `schedule(at=...)`, and `schedule()` defaults to `port=8000`. With `$PORT=8871` (the bench sets
   it) the app had two listeners, so `APP.asgi()` refused to build and every route test failed.
   A1 spent T2's and T5b's middle turns finding that `env -u PORT` made the tests pass. In
   production, the clock would start a second server on 8000. **Fixed in the K4 PR:** in a served
   project, `add job --at/--every` writes `port=PORT`, and the recipe says why.
3. **Reading before writing.** T1 still opens with two `cat` calls over the whole template, then
   edits the files `add` wrote. K4 (one file per feature) is the lever for this.
4. **Lint C16 on `SPEC.md`.** The bench copies SPEC.md into the project root, and `operonx check`
   reports it. One track spent a turn on it. This is a bench artefact, not a product defect.

**Also found, not fixed (Backlog):** uvicorn 0.54 (what `uv sync` resolves today) removed
`Multiprocess(target=)`, so `operonx/app/serve/balance.py` cannot start a pooled listener
(`workers>1`). `test_serve_runs_a_pooled_listener_as_worker_processes` fails on a fresh sync and
passes on uvicorn 0.46.

**Next.** Step 3 says to re-run A' when K4 changes the template, and it does. That re-run (about
$3.40) measures K4 together with the two fixes above. Spend so far: $1.21 + $3.41 = $4.62 of the
$15 cap. With the re-run and gate 2 (about $6), the total is about $14.

## K4 result

Done 2026-11-15, board C36. Before/after tree, flows and code changes: [cost/K4.md](cost/K4.md).

- **Shipped.** `operonx init` writes the feature as one file, `src/<feature>.py`: ops, graphs,
  `JOBS`, `SERVICES`, in that order (hello: 5 files → 1, chat and agent: 6 → 1). `app/main.py`
  imports `from <feature> import JOBS, SERVICES`. `operonx add` writes into the file (code above
  the lists, the entry into its list) or, for a feature split into `src/<feature>/`, into the
  layered files. Lint C13 counts `src/F.py` as a feature. AGENTS.md says when and how to split
  (~300 lines, `__init__` re-exports the lists) and points to `05-project-layout.md`, whose worked
  project is now one file.
- **Scope added, flagged:**
  - The two gate 1 causes (`"$body"`, the clock's port), above. Both were measured costs on the
    tasks K4 is judged by, so the re-run should not pay for them again.
  - A bug on main: `.gitignore`'s `lib/` had excluded `templates/_common/web/lib/api.js.tmpl`
    since #270. A `--web` project built from git or a wheel imports a missing `lib/api.js`. The
    bench did not catch it because it runs on the on-disk checkout, where the file existed
    untracked.
- **Verified.** All six projects (hello, chat, agent; bare and `--http --web`) pass
  `operonx check --call`, tests included. tests/internal/cli, lint, guide and app: 958 passed. The one failure
  is the uvicorn 0.54 pooled-listener break in `balance.py`, a file this PR does not touch. The guide snippet
  pages pass (10).
- **Not measured yet.** The A' re-run (step 3) is next.


## A' re-run after K4

Run 2026-11-15 on main `3d18ab9` (K4 + the gate 1 fixes), 2 tracks × T1–T6 of arm A', $2.23.
Rows: [bench/token_ab/results/2026-11-15-k4-rerun.jsonl](../bench/token_ab/results/2026-11-15-k4-rerun.jsonl).

**Gate 1 still fails, by less.** A' passed 14/14 on the first try (no fix turns, against 2 before)
and cost $2.23, down from $3.41. The bar is A' ≤ B, and B is $1.31: A' is 1.7× B (it was 2.6×).

| task | A' after K4 | A' gate 1 | B |
|---|---|---|---|
| T1 new service | $0.42 · 28 turns | $1.31 · 62 | $0.21 · 11 |
| T2 job + route | $0.31 · 20 | $0.36 · 23 | $0.17 · 9 |
| T3 report job | $0.23 · 11 | $0.33 · 21 | $0.18 · 10 |
| T4 | $0.20 · 11 | $0.28 · 18 | $0.17 · 14 |
| T5a retry | $0.42 · 34 | $0.37 · 34 | $0.20 · 21 |
| T5b retry | $0.38 · 28 | $0.47 · 39 | $0.23 · 19 |
| T6 page | $0.26 · 20 | $0.29 · 23 | $0.15 · 9 |
| **all** | **$2.23 · 152** | $3.41 · 220 | **$1.31 · 93** |

Where the remaining gap is, from the session logs:

1. **T1 tears down the scaffold first.** Both tracks removed the `hello` greeting (the file, its
   test, its import and its job in `app/main.py`) and pointed `DATA` at `$DATA_DIR` before writing
   any ticket code. B starts empty and pays none of this. A2 also left `*TICKETS_SERVICES` twice in
   `app/main.py` with its own string replace and spent a turn on it (not an `operonx add` bug:
   reproduced, `add` wires the list once).
2. **Testing a failure costs more than fixing it (T5a).** The retry itself is one line in
   `resources.yaml`. A2 then spent ~15 turns proving it in a test: the fake model can answer
   `{status: 503}`, but its script is set once per session by `conftest.py`, so the agent read
   `ResourceHub` and the run store to count calls.
3. **Manual smoke runs.** Both T1 tracks started `./run.sh`, curled it and killed it by hand after
   `operonx check` had passed.

## Gate 2 result (E7b)

Run 2026-11-15, harness change and before/after: [cost/E7b.md](cost/E7b.md). Three new tasks after
T6, each track in its own T6 workspace (A' from the re-run above, B from the 2026-11-15 E7 run), $2.41.
Rows: [bench/token_ab/results/2026-11-15-gate2.jsonl](../bench/token_ab/results/2026-11-15-gate2.jsonl).

**Gate 2 failed.** All 12 sessions passed on the first try. On the tasks where reuse should win,
A' cost 2.5× B:

| task | A' | B |
|---|---|---|
| T7 `GET /timings` (trace a slow request) | $0.24 · 14 turns | $0.21 · 11 |
| T8 SSE stream of a model's summary | $0.81 · 49 | $0.21 · 10 |
| T9 scheduled job + resumable job | $0.67 · 46 | $0.27 · 14 |
| **all** | **$1.72 · 109** | **$0.68 · 35** |

The causes, from the session logs, in order of cost:

1. **The built-in SSE does not fit a plain text stream (T8, $0.60 over B).** An http door with
   `Accept: text/event-stream` JSON-encodes each event and sends no closing event. Both tracks
   found that, dropped to a raw `asgi()` mount, and read operonx's source to stream from a graph by
   hand: 14 of A2's 23 calls opened `operonx/` files. The guide and the recipes have no
   "stream a model's text to the caller" entry.
2. **A test cannot script the model for itself (T9, T5a).** `operonx.bootstrap` is idempotent, so a
   test that wants the fake model to fail cannot replace `conftest.py`'s script. A1-T9 tried
   `ResourceHub.set_instance`, leaked the hub into other tests, then edited the live fake's script.
3. **`operonx check` ends red on things the agent did not cause.** C16 on `SPEC.md` (bench
   artefact), and the probe of an `asgi("/summaries/")` mount fails because `/summaries` is
   answered by the StaticFiles at `/`. Both tracks spent their last turns on these and still
   reported FAIL.
4. **`env()` casts by the default's type.** `env("SUMMARY_EVERY", 3600)` makes an int, and lint's
   offline build fills every variable with a placeholder string, so the build failed. The agent
   passed a string default.

## Outcome

Both gates failed. After K1–K5, an operonx project still costs 1.7× an empty folder on the E7 tasks
and 2.5× on the reuse tasks, though every session now passes first time (A: 13/14 with 101k doc
tokens read; A': 14/14 and 12/12 with 15k). The plan's own rule for a failed gate 1 is that
`operonx init` defaults to the lean form, which K4 made the default.

Spent $9.26 of the $15 cap ($1.21 pilot, $3.41 gate 1, $2.23 re-run, $2.41 gate 2).

The four causes above are cards C39–C42 in the board's Backlog, each with its measured cost. They
are not in this plan: the owner decides whether a second round (about $5 to re-measure) is worth it.

## Round 2 (the owner chose it, 2026-11-15)

Fix the four measured causes, then run A' once more on all ten tasks (T1–T6 for gate 1, T7–T9 for
gate 2) and compare it with the B rows already measured (gate 1 B $1.31, gate 2 B $0.68). Budget:
the $5.74 left of the cap; the run stops at that.

Each cause was reproduced in the A1/A2 workspaces before this plan was written:

| card | reproduced | fix |
|---|---|---|
| C42 | `env("SUMMARY_EVERY", 3600)` raises under the build's placeholder; both tracks wrote a workaround (`_seconds()`, a str default then `isdigit()`) | the build fills a name from `.env.example`'s own value when it has one; `env()` with a number or bool default answers the default for the placeholder |
| C41 | `operonx check` in A1: C16 on `SPEC.md`, `run.sh`, `job.sh`; `GET /summaries/` "only /summaries/ reaches function at /summaries" | C16 allows root `*.md`, `*.sh`, `Dockerfile`, compose files; the trailing-slash probe skips a mount of an app with no routes (nobody calls its bare prefix); GET routes are called with the fake model too (a GET that streams a model's text would otherwise call the real one with placeholder credentials) |
| C40 | A2-T5a/T9: `ResourceHub.reset_instance()` + `bootstrap()` inside a test, restored in `finally` | `operonx.testing.fake_model(*script, key="llm:assistant", **config)`: a context manager that swaps one `llm:` resource for a scripted fake in the installed hub and puts the old one back; it yields the fake, whose `.calls` the test reads. AGENTS.md's Test recipe shows it |
| C39 | a stream door on a doorless graph with `egress(item=s["content"])` sends `"SUM"` (JSON), then the egress op's own output `{"sent": [...]}` as a last event, and no closing event; it streams only with `Accept: text/event-stream` | `http(..., sse=True)`: always answers `text/event-stream`, a string item as its text, then `event: done`; a graph that sends with `egress` replies with what it sent, never also its outputs. AGENTS.md recipe "Stream a model's text", guide 02 § Service |

Not in this round: the T1 scaffold teardown (A' removes the greeting and repoints `DATA`), which is
a template question, and C38.

**Built** (C39–C42 done): the fixes above as written, with one test per cause, each seen to fail
with its fix reverted; both new recipes run in a generated `chat` project. Write-up with the
before/after flow and the code changes: [cost/R2.md](cost/R2.md).

## Round 2 result

Run 2026-11-15 on main `b381ceb7` (C39–C42), 2 tracks × T1–T9 of arm A', every session passing
first time (20/20, no fix session). Rows:
[bench/token_ab/results/2026-11-15-round2.jsonl](../bench/token_ab/results/2026-11-15-round2.jsonl).
Turns are counted over every result event of a session (`run.py` counted only the last one, which
undercounts a session that waited on a background task); the cost was right before.

| task | A' round 1 | A' round 2 | B |
|---|---|---|---|
| T1 new service | $0.42 · 28 | $0.35 · 20 | $0.21 · 11 |
| T2 | $0.31 · 20 | $0.33 · 27 | $0.17 · 9 |
| T3 | $0.23 · 11 | $0.29 · 19 | $0.18 · 10 |
| T4 | $0.20 · 11 | $0.24 · 15 | $0.17 · 14 |
| T5a retry (C40) | $0.42 · 34 | **$0.17 · 11** | $0.20 · 21 |
| T5b | $0.38 · 28 | $0.31 · 25 | $0.23 · 19 |
| T6 | $0.26 · 20 | $0.24 · 15 | $0.15 · 9 |
| **gate 1** | $2.23 · 152 | **$1.94 · 132** | **$1.31 · 93** |
| T7 trace | $0.24 · 14 | $0.25 · 10 | $0.21 · 11 |
| T8 stream (C39) | $0.81 · 49 | **$0.32 · 21** | $0.21 · 10 |
| T9 clock + resume | $0.67 · 46 | $0.72 · 52 | $0.27 · 14 |
| **gate 2** | $1.72 · 109 | **$1.29 · 83** | **$0.68 · 35** |

**Both gates still fail.** Gate 1 is 1.5× B (was 1.7×), gate 2 1.9× (was 2.5×).

- **What the fixes bought.** T8 fell from $0.81 to $0.32: both tracks used `sse=True` and nobody read
  operonx's source. T5a fell from $0.42 to $0.17, below B: the retry test is one `fake_model` block.
- **T9 is a bug in the C40 fix.** Both tracks wrote the resume test with `testing.fake_model` after
  `conftest.py`'s session fixture had warmed the engine. An `LLMOp` took its model from the hub once
  (`_ensure_initialized`) and kept it, so the block's script never reached it; A1 read `testing.py`
  and `llm.py` and then built the app inside the block. Fixed after the run: `ResourceHub.revision`
  goes up on every swap and the op takes its model again
  (`test_an_engine_that_already_ran_answers_from_the_block`, seen to fail without it). Not
  re-measured: at B's own T9 cost, gate 2 would still be about $1.07 against $0.68, and gate 1 has
  no T9.
- **What is left is turns, not context.** A turn costs the same in both arms ($0.0147 in A' against
  $0.0141 in B on gate 1; $0.016 against $0.019 on gate 2). A' takes 42% more turns on gate 1 and
  2.4× on gate 2. AGENTS.md and the guide no longer make each turn dearer. The extra turns are
  verifying: every A' session ran `operonx check`, 41 calls in 20 sessions (1–5 each, again after
  each fix), with the project's tests beside it, and T1 still tears down the scaffold (A' removes
  the greeting and repoints `DATA`, left out of round 2). Outside the two T9s, operonx's source was
  read once per track: a `grep "def env"` in T1 (`env()`'s signature is not in AGENTS.md). No
  session wrote a C41/C42 workaround.

**Outcome.** The plan's rule for a failed gate 1 stands: `operonx init` defaults to the lean form
(K4). The run was cut once after seven tasks (the driver was killed with the shell that started it);
the two sessions in flight ($0.46) count in the spend but not in the rows, and the bench resumed
each track at its last commit with the new `run.py --start TRACK=TASK`. Spent $3.68 this round,
$12.94 of the $15 cap. Write-up: [cost/R2.md § Measured](cost/R2.md#measured).

## Round 3: why A' costs more, and what cutting it costs in quality (owner, 2026-11-15)

The owner's theory: on a large project operonx should cost less, because the agent reuses more. The
owner's question: if we cut A's tokens, do we lose correctness or stability?

**What the round-2 logs already show** (free, no model calls):

- A' writes about the same feature code as B. Tasks T2–T9, per track: A' 357–412 lines, B 275–369.
  On top of that, A' writes ~230 lines of tests; B writes none.
- A' made 35 test calls and 47 `operonx check` calls; B made 1 and 0. B checks its work with curl
  (41 calls).
- Those calls account for about the whole gap: roughly 70 of A's 215 turns, at $0.015 per turn.
- Reuse shows in lines only on T8: A' 23–33 lines, B 74–79. A' still cost 1.6× there (6 test runs
  and 6 `check` runs).
- A'/B by task: 1.7 2.0 1.6 1.4 0.9 1.4 1.6 1.2 1.6 2.7. No downward trend.
- B's whole app is 363–505 lines at T9. The project never gets large enough for reuse to pay.
- **Quality baseline.** All four round-2 final workspaces pass every hidden check, T1–T9 (40/40).
  B's zero tests cost it nothing on the current checks. Those checks are too lenient to measure
  quality, so this round adds harder ones first.

| step | what | spend |
|---|---|---|
| Q | `bench/token_ab/hard.py`: hidden robustness probes, run after a track. Restart keeps data, bad input is a 4xx and not a 500, 20 parallel creates, a model outage survived, a bad date, unicode, a job on an empty store, and every earlier task's check (regression). Baseline on the round-2 workspaces. | $0 |
| L | Arm L = A' with step 3 of AGENTS.md changed: verify by hand (run it, curl it), write a test only when the task asks, `operonx check` only to explain a failure. 2 tracks × T1–T9. | ~$3 |
| G | Growth tasks T10–T17 (agents and assignment, reply drafts + stream, urgency + filters, SLA escalation job, webhook with retry, search page, CSV + per-agent digest, ticket history), each with a hidden check. Runs from each arm's T9 workspace: A' ×2, L ×2, B ×2. | ~$13 |

**Gates.** Cost alone decides nothing; a cheaper arm counts only if its quality holds.

- **Quality is a gate.** First-try pass rate, fix sessions, and the hard suite plus regression after
  T9 and after T17. An arm that loses any probe that another arm passes has lost quality, and its
  cost is not a win.
- **L answers "conventions or framework".** If L ≈ B on cost with A'-level quality, the tests and
  `check` cost the money, not operonx. If L ≈ A', the framework costs it.
- **G answers "does reuse pay as the project grows".** A'/B and L/B per task on T10–T17, against
  project size. The theory holds if the ratio falls below 1 on the growth tasks.

Cap $30 for the round (the owner's $40 rule). Write-up: [cost/R3.md](cost/R3.md).

## Round 3 result

Run 2026-11-15, 2 tracks per arm. Spent **$17.16** of $30: L $3.34, G $7.37, a G run spoiled by a
bench bug $6.35, a namespace smoke test $0.10. Write-up: [cost/R3.md](cost/R3.md) (tables, the
same feature in both arms, the bench bug). Rows: `bench/token_ab/results/2026-11-15-round3-*.jsonl`.

| | first try | hard probes | regression T1–T17 | $ T1–T9 | $ T10–T17 |
|---|---|---|---|---|---|
| A' | 35/36 | 8/9 ×2 | all pass | $3.23 | $3.47 |
| L | 34/36 | 9/9, 8/9 | all pass | $3.34 | $3.90 |
| B | 36/36 | 9/9 ×2 | all pass | $1.99 | $2.16 |

- **Quality gate.** L kept A's quality (one more fix session out of 36, inside the noise) but did not
  get cheaper, so it is no win. B has the best quality on every measure with 0 tests.
- **L: conventions or framework?** Both. Step 3 cut `operonx check` calls 47 → 14, yet pytest runs
  went 40 → 44: the rest of AGENTS.md and the scaffold's test still pull the agent to write tests.
  And operonx code is ~1.5× B's for the same features (T10–T17: ~700 lines vs ~460), an op + graph +
  `Service` per route.
- **G: does reuse pay as the project grows?** No. A'/B stays at 1.6 (T1–T9 1.62, T10–T17 1.61) as
  the app grows from ~550 to ~1250 lines; no later task reused an earlier graph.
- **Found on the way.** (1) The http door does not check a body field's type: `{"text": 5}` is
  stored with a 201 on 3 of 4 operonx tracks (B: 400). (2) The bench let one track's
  `pkill -f "operonx serve"` kill another's server; sessions now run in their own pid namespace.

Not changed in operonx this round; what to do about (1), the per-route wrapper and the test pull is
the owner's call.
