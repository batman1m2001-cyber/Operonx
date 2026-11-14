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
first-turn context of an A session before cutting.

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
