# Agent tools as ops, and the agent card

**Status:** plan, 2026-10-09. Phases T1–T4 below.

**Asked for:**
- An agent's tools can be operonx ops and graphs, and the trace shows what ran inside them.
- The agent op is the studio's standout card. It folds and unfolds like a graph op, and shows the
  agent's parts: instructions, memory, context and tools.
- Toy project: `meeting-prep-operonx`.

**Mock:** https://claude.ai/artifact/VWWiB3S3wpruK8jXnPGsjc. It shows:
- the bee icon;
- the folded and unfolded card;
- the running states;
- the theme options.

## 1. Decisions

| # | Decision | Choice | Evidence |
|---|---|---|---|
| **E1** | Does the agent loop become a graph back-edge? | **No.** It stays plain `async` code in one op, as `AGENTS_V2_PLAN.md` D1 decided. | §2a: re-measured today, the back-edge loop is 7× slower per turn at 5 concurrent runs and fails D1's 2 ms gate again. The user chose to keep the loop where it is (2026-10-09). |
| **E2** | How does a tool run an op or a graph? | Through the engine, as a nested run. Its records join the caller's trace under the tool's step. | §2b: today a nested run records into a private trace that nobody reads. |
| **E3** | Where does a nested record find its parent? | `build_tree` gets one rule: a record sits under the nearest record whose full name and ctx it extends. | The studio, Langfuse and the run stores all read `build_tree`, so one change covers them all. |
| **E4** | What happens when an op body calls an `@op` function directly? | Raise a clear error. Today it silently returns a `FuncOp`. | §2b |
| **E5** | Agent card colours | The bee stays. The card uses one calm theme; the default is **steel**, until the user picks another. | The user rejected violet and gold ("easy to look at"). |

## 2. What was measured

### a) Agent loop: plain code in one op vs a graph back-edge

**Setup:**
- Script: `scratchpad/agentgraph/bench_loop2.py`.
- Each turn does the same work in both loops: a scripted 0 ms model, one tool call, and three trace
  records (turn, model, tool).
- Tracing is on. 200 runs per cell, after 20 warm-up runs.
- operonx is 1.17.9.

| loop | ms per turn, 1 run at a time | ms per turn, 5 concurrent, p50 / p95 | event-loop lag p99 at 5 concurrent |
|---|---|---|---|
| plain code in one op (today) | 0.14 | 0.45 / 0.55 | 1.96 ms |
| graph back-edge | 0.77 | **3.35 / 6.28** | 2.85 ms |

The A0 spike's own benchmark cannot run any more: it imports the deprecated `operonx.agents` react
graph.

### b) A tool that uses an op or a graph, today

**Setup:**
- Script: `scratchpad/agentgraph/tool_calls_op.py`.
- An `AgentOp` inside a graph, with two tools:
  - one calls an `@op` function;
  - one runs a `@graph` with `Operon(...).run()`.

**Results:**
- **The `@op` call:** it returns a `FuncOp`, an op definition, instead of running the function. The
  model is told "returned FuncOp".
- **The graph run:** it returns the right value (`"A1: shipped"`), but its op `lookup` is not in the
  trace:
  - `engine.start` inside a running op records into the nested handle's own trace and calls no
    consumer (`engine.py`, the `nested_in` branch).
  - So the parent trace ends at the tool's step.

## 3. Phases

### T1: operonx — nested runs in the caller's trace (operonx 1.18.0)

**Work:**
1. **Nested records join the caller's trace.** When a run starts inside an op body, each record it
   makes is copied into the caller's trace with:
   - its `op_full_name` prefixed by the calling frame's full name;
   - its ctx prefixed by the calling frame's ctx, with the nested run's own leading `"main"`
     dropped.

   Task events (`TaskStarted` and so on) are forwarded the same way, so a live studio sees them. The
   nested handle keeps its own trace, as it does today.
2. **`build_tree`.** A record that is not a `child()` step looks for its owner first: the nearest
   record whose full name and ctx are dot-prefixes of its own. The graph containers between the
   owner and the record are named from the full-name segments in between.
3. **`invoke(target, **inputs)`.** It runs an `@op` function or a `@graph` from inside an op body,
   as part of the current run, and returns its outputs dict.
   - A bare `@op` gets a one-node graph, built once per target and cached.
   - An engine is built once per target and cached.
4. **The `@op` trap.** Calling an `@op` function while an op body is running, rather than while a
   graph is being built, raises `TypeError` and points to `invoke`.
5. **Docs and tests:** guide page `01-ops.md` or `02-composition.md`, the CHANGELOG, and tests:
   - the nested records' names, ctx and tree parents;
   - a nested graph that holds a subgraph, a generator and a loop;
   - a nested failure, and a cancel;
   - the `@op` trap.

**Gate:**
- The full suite passes.
- `tool_calls_op.py` shows `lookup` under the tool's step in `build_tree`.
- No change to the trace of a run that starts no nested run: the records of a fixed example graph
  compare equal before and after.

### T2: operonx-agents — ops and graphs as tools (operonx-agents 0.2.0)

**Work:**
1. **Accepted as tools:**
   - `Agent(tools=[...])` and `Toolset` accept an `@op` function or a `@graph`, beside `@tool`
     functions.
   - `tool(op_or_graph, readonly=True, ...)` sets the flags for any of them.
   - The schema comes from the target's parameters and its docstring's `Args:` section, as for
     functions.
2. **Running one:** a call runs through `invoke` inside the tool's existing `child()` step. Every
   dispatch rule stays as it is: policy, validation, hooks, approval, timeout, truncation and
   concurrency.
3. **What the model reads:**
   - an outputs dict with one key gives its value;
   - otherwise the dict, as JSON.
4. **`AgentOp.specific_metadata`** gives the studio the agent's anatomy:
   - name, model and settings;
   - instructions: the text, or "built per run" with the function's name;
   - output type, limits, policy and context policy;
   - session kind and approval TTL;
   - the tools: for each, its name, description and kind (`function` / `op` / `graph` / `agent` /
     `mcp`) and its flags. For an op or a graph, also the target's IR, so the studio can draw it.

**Gate:**
- Every agents test passes.
- New tests:
  - an op tool and a graph tool, run by `Runner.run` and by `AgentOp`;
  - their trace: nested under the tool's step;
  - a failing op tool, which the model reads as an error message;
  - an op tool that needs approval;
  - the metadata.
- The `bench_overhead.py` A5 numbers for function tools stay within noise.

### T3: operonx-studio — the agent card (branch `feat/agent-card`)

**Work:**
1. **The bee:** the image (light and dark versions) goes in `static/`.
2. **Folded card:** bee, name, model chip, turn pips, and an icon row for instructions, memory,
   context and tools. It uses the chosen theme in both studio themes.
3. **Unfolded card, like a GraphOp:**
   - **setup chips:** instructions, deps, session, context. Each opens in the side panel.
   - **the loop:** input → model → which tools? → one node per tool → a back edge, plus "done" →
     output.
   - **a tool that is an op or a graph** opens to its own graph.
4. **A run painted on it:**
   - the turn pips fill in, and the tool being called glows with its call count;
   - the folded card's border sheen runs while the agent runs;
   - a nested tool graph's records paint on that tool's graph.
5. **`agentsteps.js`** reads nested tool records (T1's shape).

**Gate:**
- JS tests pass, and `layout_audit` reports 0 errors.
- Screenshots on desktop and phone, in both themes, of:
  - the folded card at rest and running;
  - the unfolded card;
  - a tool graph opened.

### T4: meeting-prep-operonx — the toy project

**Work:**
1. The researcher's `web_search` and `fetch_page` become `@op`s.
2. One tool is a small `@graph`, so the studio shows a graph tool. The candidate is the page fetch:
   fetch → visible text → trim.
3. Run the `prepare` service on a sample email, and open the run in the studio.

**Gate:**
- The project's tests pass.
- One real run, using its configured `assistant` resource.
- Screenshots of the run in the studio: the agent unfolded, and a graph tool opened.

## 4. Order and releases

T1 → T2 → T3 → T4. Each phase is its own PR, merged when its gate passes.
- operonx 1.18.0 ships after T1.
- operonx-agents 0.2.0 ships after T2. It requires `operonx>=1.18.0`.
- The studio is not published (`install.sh`).
