# State model

Every op has a typed input and output dict. Edges between ops are wired by
**reference**, not by string lookup at runtime.

## Cell layout

State lives in a triple-keyed cell map: `(op_full_name, var_name, context_id) → Value`.
The context tuple is how parent ↔ child boundaries stay correct in
nested `@graph`s and how generator-op iterations stay isolated:

```mermaid
flowchart LR
    subgraph PARENT["parent: GraphOp 'main' &nbsp;(context = ())"]
        P_score["('main', 'score', ())"]
        P_size["('main', 'size', ())"]
    end

    subgraph CHILD["child: nested @graph 'verify' &nbsp;(context = ('verify',))"]
        C_grade["('verify.cls', 'grade', ('verify',))"]
        C_score["('verify.cls', 'score', ('verify',))"]
        C_out["('verify.work', 'trace', ('verify',))"]
    end

    P_score -->|"verify(score=PARENT['score'])"| C_grade
    P_size -->|"verify(size=PARENT['size'])"| C_score
    C_out -->|"work['trace'] >> PARENT['trace']"| P_size

    classDef parent fill:#ede7f6,stroke:#5e35b1,color:#311b92
    classDef child fill:#e0f2f1,stroke:#00897b,color:#004d40
    class P_score,P_size parent
    class C_grade,C_score,C_out child
```

Three rules fall out of this layout:

- **`PARENT["k"]` reads from the enclosing context.** From the child's
  point of view, `PARENT` is the immediate parent — the runtime walks
  the context tuple upward until it finds `k`.
- **`op["k"]` reads from a sibling at the same context.** No walk —
  same context, different op name.
- **`op["src"] >> PARENT["dst"]` writes upward.** The scheduler emits
  the frame to both the child's own state and the parent's slot.

## PARENT vs op["key"]

**Use `op["key"]` to pass data between sibling ops. Use `PARENT["key"]`
only for external inputs** (from `engine.run()` or from the parent graph
in nested contexts).

```python
# CORRECT — read from sibling op's output
g = greet(name=PARENT["name"])      # PARENT["name"] = external input
u = upper(text=g["greeting"])       # g["greeting"] = sibling op output
START >> g >> u >> END

# WRONG — PARENT["greeting"] doesn't exist; g didn't forward there
u = upper(text=PARENT["greeting"])  # greeting is in g's state, not parent
```

| Reference | Source |
|---|---|
| `PARENT["k"]` | External inputs from `engine.run(inputs={...})` or the parent graph |
| `op["k"]` | Output from a sibling op within the same graph |
| `>> END` | Auto-forwards the last op's outputs to the graph result |

## Output mapping

Two equivalent styles. Pick whichever reads clearer:

```python
# Style 1 — outputs= parameter (inline with op creation)
llm = LLMOp.of(
    resource="gpt-4o",
    messages=p["messages"],
    outputs={"content": PARENT["answer"]},
)

# Style 2 — >> operator (standalone, equivalent)
llm = LLMOp.of(resource="gpt-4o", messages=p["messages"])
llm["content"] >> PARENT["answer"]

# Wildcard — forward all outputs
step = process(x=PARENT["x"], outputs={"*": PARENT})
```

The `>>` form is common inside loops where you want to update loop state
or forward the loop's final result.

## Schema

Every op declares a schema based on its function signature and return
annotation. The graph builder uses these schemas to:

1. Validate that `op["k"]` references an output the source op actually
   produces.
2. Validate that `PARENT["k"]` references an input the engine will be
   given.
3. Pre-compute the runtime mapping so frame propagation is O(1).

If your op returns dynamic keys, declare them with `outputs=[...]` on the
op decorator or constructor — runtime cannot infer them otherwise.

## Frames

Each op produces one or more **frames** during execution. A frame is a
snapshot of the op's output at one point in time. Most ops produce a
single frame; generator ops produce one per `yield`. The scheduler emits
frames downstream as they appear, which is what enables streaming
(see [Streaming](streaming.md)).

State within a graph is per-frame. When a generator op yields three
values, downstream ops see three independent state slices, run in
parallel by default.
