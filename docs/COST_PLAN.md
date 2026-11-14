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
