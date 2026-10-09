# Agent tools as ops, and the agent card

**Status (2026-10-09): ALL DONE.**
- T1: operonx 1.18.0 (#131) and 1.18.1 (#133: `WorkflowTrace.parent` / `.root`).
- T2: operonx-agents 0.2.0 (#132).
- T3: studio #37 (the hive cell), then T3b in studio #38 (§5: the agent's parts as real ops).
- T4: meeting-prep #9 (`web_search` an @op, `fetch_page` a @graph).
- The live studio (:8766) runs #38.

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

## 5. T3b: the agent's parts as real ops (the design agreed on 2026-10-09)

Studio #37 drew an opened agent as a frame round its loop (model → tools → answer). The user replaced
that design, after many rounds of mocks.

**Mock:** https://claude.ai/artifact/VWWiB3S3wpruK8jXnPGsjc, version 22, section "With the studio's
real cards".

### The design

**The agent card:**
- An octagon "hive cell": a steel edge (#4a6fa5, light #c9dcf3), a soft glow, the bee
  (`static/agent-bee*.png`), its name, "agent · ≤ N turns" and turn pips.
- Three ROUND sockets on its right edge, each with an icon: ✧ model (violet #7c4dcc, required), a
  database cylinder for memory (blue #2f6fd0), ⚒ tools (teal #0e8580).

**Parts are real ops:**
- Each part is the canvas's own card for that op, drawn by the same `opCard()`:
  - the LLM cell (assistant);
  - a FuncOp cell (`web_search`);
  - a GraphOp card with its ▣ N ▸ badge (`fetch_page`);
  - an MCP or IO cell;
  - an agent card, for an agent tool.
- The parts stand in one column to the right of the agent. Control flow runs straight down through
  the agent.

**Opening a part reuses what exists:**
- A `@graph` tool opens as the usual graph container.
- An agent tool opens as an agent with its own sockets and parts, recursively.

**The wires are thin (about 1.3px) and new.** Control flow (the 4px energy beam) and data flow are
NEVER changed.

| Part | Wire | Idle | While a run is live |
|---|---|---|---|
| model | a "brain signal": a violet nerve with one EEG-like spike mid-way | the spike shows | impulse dots run to the LLM |
| memory | "bandwidth": three thin blue lanes (0.9px, ±2.6px) | the lanes show | data blocks stream into the store |
| tools | a rack: one straight trunk from the socket into a vertical bus beside the tool cards, and a straight branch with a joint dot to each tool | straight lines | a call dot goes socket → bus → tool and back |

Each wire ends in a small ring on the part's card.

**Rejected, do not bring back:**
- the frame round the loop;
- a parts tray, a drawer, n8n-style dashed curves, abstract shapes;
- beads, cable bands, twin lines, a pipe, rail crossties, a sine wave, `››`;
- any change to the existing edges.

### The work

1. **Extractor (`operonx_project/extract.py`):**
   - Replace `_agent_loop` with the parts.
   - The model becomes a node for the LLM resource.
   - Memory becomes the session / deps.
   - Each tool becomes a real node: a `@graph`'s subgraph via `_tool_graph`, an `@op`'s code, an
     agent tool's own agent node, recursively.
   - Each part is an edge `{type: "agent_part", part: "model" | "memory" | "tool"}` from the agent.
2. **Layout (`flowlayout.js`):**
   - The agent and its parts are one block: the card, then the parts column to its right, at the
     card's rank.
   - Opening a part grows the block.
   - The main flow stays straight.
3. **Canvas (`studio.js` / `studio.css`):**
   - the round sockets on the card's right edge;
   - the three wire styles, drawn by the edge router as their own class;
   - the rack routing for tools;
   - the run painting: counts on the parts, the wire animations only while the run is live.
4. **The run view** keeps the recorded turns (agentsteps.js).
5. **Gate:**
   - the JS tests;
   - layout_audit (agentlab plus the 174-case matrix);
   - screens on desktop and phone, light and dark, of a folded agent, a graph tool opened, an agent
     tool opened, and a painted run.

### How it was built (studio #38)

- **Extractor:** `_agent_parts` gives an agent node `parts`: the model (an LLMOp), its memory (the
  op's `sessions=` store; none, and the memory socket is shown empty), and each tool as the op it
  is. An agent tool brings its own parts (3 levels at most). The run view still opens an agent onto
  its recorded turns.
- **Layout:** an opened agent is one block whose axis is the card's centre. Parts stand in a column
  `PART_X` to the right. A loop's return goes round the card's top and foot, never across the parts.
- **Gate passed:**
  - JS 109 (two new layout tests, one of them over 160 random workflows with agents);
  - Python 678;
  - layout_audit: agentlab 29 cases and the matrix 174 cases, 0 errors;
  - screens checked in light, dark and phone, on agentlab and meeting-prep.
