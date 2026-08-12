# Patterns

A reference card for the DSL — every shape you'll write in everyday
Operonx code, in one place. Each section is short, with a snippet you
can copy. For end-to-end walkthroughs see [First workflow](01-first-workflow.md)
through [Tracing](07-tracing.md).

## `@op` — turn a function into a node

```python
from operonx.core import op

@op
def double(x: int):
    return {"result": x * 2}
```

Rules:

- Return a dict. The keys are the op's output variable names, addressed
  downstream as `op["key"]`.
- Type-annotate parameters when you can — Operonx coerces JSON inputs
  through the annotations.
- `@op` works on both sync and `async def`.
- For per-item iteration, write a generator (`yield {...}`) and the
  scheduler dispatches downstream ops once per yield. See
  [Iteration patterns](#iteration-patterns) below.

## `GraphOp` — collect ops into a DAG

```python
from operonx.core import GraphOp, START, END, PARENT

with GraphOp(name="workflow") as g:
    step = double(x=PARENT["input"])
    START >> step >> END
```

Inside the `with` block, every op constructor (`double(...)`) attaches
the op to the graph being built. `START >> a >> b >> END` chains ops
into edges. The `name=` argument is the graph's identity in tracing
and error messages.

## `@graph` — modular, reusable subgraphs

Turn a builder function into a `GraphOp` factory:

```python
from operonx.core import graph, op, START, END, PARENT, GraphOp

@op
def detect_card(conversation: str):
    return {"has_card": "card" in conversation}

@graph
def verify_card(conversation):
    check = detect_card(conversation=conversation)
    START >> check >> END
```

Use it like a function — when called inside another `with GraphOp`,
its parameters become PARENT refs automatically:

```python
with GraphOp(name="main") as g:
    v = verify_card(conversation=PARENT["conv"])
    START >> v >> END                                     # v.name == "v"
```

What `@graph` gives you:

- Function params → `PARENT` refs (injected at the call site).
- Auto-naming from the variable (`v` here) — override with
  `verify_card(..., name="checker")`.
- `>> END` auto-forwards the last op's outputs to the subgraph result
  via the inner GraphOp's auto-populated outputs schema.

## `Op.of()` — concise op creation

For framework-provided ops (`LLMOp`, `EmbeddingOp`, `RerankOp`, etc.),
prefer the `.of()` classmethod with explicit keyword arguments:

```python
from operonx.providers import LLMOp, EmbeddingOp

# Provider ops with .of()
llm = LLMOp.of(resource="gpt-4o", messages=PARENT["msgs"])
embed = EmbeddingOp.of(resource="bge-m3", texts=PARENT["texts"])

# Prompt template + LLM in one op — prompt= takes str or dict;
# a ready message list goes to messages= and is never formatted
c = LLMOp.of(
    resource="gpt-4o",
    prompt={"system": "...", "user": "{q}"},
    q=PARENT["q"],
)
```

Never positional. The keyword form catches typos at construction time
rather than at runtime against a wrong parameter name.

## Edge types — `>>` and `~`

An edge decides **when a node becomes ready**. Two kinds:

- **Hard `>>`** — the default. The target waits for *every* hard
  predecessor.
- **Soft `~`** — the target fires when **any one** soft predecessor
  completes. All soft predecessors of a node collapse to a single
  ready-count.

```python
a >> b                      # b waits for a
a >> ~d                     # d fires when any one soft pred finishes
c >> ~d                     # …so a or c, whichever lands first
```

Soft edges exist for two *different* reasons, and conflating them is why
`~` used to be everywhere. They are worth keeping apart.

### 1. Branch merges — derived, never written

When a branch routes to one of several arms and those arms fan back into a
merge, the arms that were not selected never run. A hard merge would wait
for them forever.

`build()` handles this. `GraphOp(auto_soft=True)` is the default, and any
two predecessors of a merge that trace back to a common branch through
*disjoint* first hops get their edges softened automatically.

```python
START >> cls >> if_(cls["score"] >= 50, passed).else_(failed)
[passed, failed] >> rep >> END        # no ~ — derived at build time
```

**Do not write `~` here.** It is the case the pass exists for, and the
manual form failed the wrong way round: forgetting it was a silent
deadlock at run time, not a build error.

Opt a specific edge out with `g.add_edge(src, dst, hard=True)`, or the
whole graph with `GraphOp(auto_soft=False)`. The edge record keeps the two
apart — `auto_soft` marks what the pass derived, `pinned_hard` what you
pinned.

### 2. Trigger control — authored, and nothing can infer it

Sometimes you want "run this as soon as *any* of these finishes" with no
branch anywhere. A race between two sources; a best-effort predecessor you
do not want to block on. Nothing in the graph shape says that — it is a
scheduling decision, so you state it:

```python
fast_source  >> ~consume
slow_source  >> ~consume       # consume runs on whichever lands first
```

That is what `~` is for, and it is the only rename-safe way to say it —
`add_edge("fast_source", "consume", soft=True)` does the same job with a
string that a rename will silently break.

### `~` on a sentinel is an error

`~END` reads like it means something and never did: edges into `END` are
unconditionally soft already, and `END` is output forwarding at build time
rather than a node the scheduler waits on. `~START` and `~PARENT` are the
same. All three raise.


## State references — `PARENT[...]` vs `op[...]`

The single most-asked rule:

> Use `op["key"]` to read another op's output. Use `PARENT["key"]`
> only for inputs that come from outside the current graph —
> `engine.run(inputs={...})` at the top level, or the parent graph's
> state in a nested `@graph`.

```python
# ✅ CORRECT
g = greet(name=PARENT["name"])         # PARENT["name"] = external input
u = upper(text=g["greeting"])          # g["greeting"] = sibling op output
START >> g >> u >> END

# ❌ WRONG — `greeting` is in g's state, not the parent's
u = upper(text=PARENT["greeting"])
```

| Reference | Reads from |
|---|---|
| `PARENT["k"]` | `engine.run(inputs={"k": ...})`, or the parent graph in a nested `@graph`. |
| `op["k"]` | The output of `op` (a sibling within the same `with GraphOp` block). |
| `>> END` | Auto-forwards the last op's outputs as the graph's result. |

## Output mapping — `op[src] >> PARENT[dst]`

Inside a graph, route an op's output up to the graph's external
state. Two equivalent styles:

```python
# Inline style — outputs= parameter at op creation
llm = LLMOp.of(
    resource="gpt-4o",
    messages=p["messages"],
    outputs={"content": PARENT["answer"]},
)

# Standalone style — `>>` operator on a separate line
llm = LLMOp.of(resource="gpt-4o", messages=p["messages"])
llm["content"] >> PARENT["answer"]
```

Use the standalone style when forwarding only a couple keys (cleaner
in loops, easier to read). Use `outputs={...}` when you're already
configuring the op and have the dict in hand.

Wildcard — forward all of an op's outputs to PARENT:

```python
step = process(x=PARENT["x"], outputs={"*": PARENT})
```

## Iteration patterns

The classic `ForOp` / `MapOp` / `WhileOp` classes were replaced by
two cleaner shapes.

### Generator ops (replaces `ForOp` / `MapOp`)

A generator op `yield`s once per item. Downstream ops fire in
parallel per yield under the streaming scheduler:

```python
@op
def each_item(items: list):
    for item in items:
        yield {"value": item}

@op
def double(value: int):
    return {"result": value * 2}

with GraphOp(name="iterate") as g:
    gen = each_item(items=PARENT["numbers"])
    step = double(value=gen["value"])
    START >> gen >> step >> END
```

Tune dispatch with `Ref.parallel(max=N)` / `Ref.collect()` on the
downstream input — see [Streaming](06-streaming.md).

### Feedback loops — a back-edge inside `@graph`

`GraphOp.loop(until=…)` and `@graph.loop(…)` were **removed in 1.0.0**. A
loop is now an ordinary edge that points backwards, and the build-time
cycle-rewrite pass turns it into a hidden loop graph for the scheduler:

```python
from operonx.core import END, PARENT, START, graph, op
from operonx.core.ops.flow.branch_op import if_


@op
def increment(count: int) -> dict:
    return {"count": count + 1}


@graph
def counter():
    PARENT.declare(count=0)
    inc = increment(count=PARENT["count"])
    inc["count"] >> PARENT["count"]        # commit for the next iteration
    START >> inc >> if_(PARENT["count"] >= 5, END).else_(inc)
```

The `else_(inc)` is the back-edge. Each iteration writes its result to the
shared cell, the branch reads the updated value, and decides to exit or go
round again.

Two things that follow from the rewrite, and are easy to get wrong:

- **Iterations are siblings, not nesting.** Iteration 1 runs at
  `("main", "g.__loop_0__#1")`, not inside iteration 0. That is what makes
  "did this op run *this* iteration" answerable.
- **The loop ends when no back-edge source fired.** There is no iteration
  counter to reason about — the branch not taking the back-edge is the
  exit.

See [Loops and branches](03-loops-and-branches.md) for the full treatment.

## Branch routing — `if_()`

Conditional dispatch through the scheduler. Pass **op instances** as
targets and drop the branch straight into the chain:

```python
from operonx.core import END, PARENT, START, graph, op
from operonx.core.ops.flow.branch_op import if_


@graph
def grade(score):
    PARENT.declare(label="")
    cls = classify(score=score)
    passed = on_pass(score=cls["score"])
    failed = on_fail(score=cls["score"])

    passed["label"] >> PARENT["label"]      # whichever arm runs
    failed["label"] >> PARENT["label"]      # writes the shared cell
    rep = report(label=PARENT["label"])

    START >> cls >> if_(cls["score"] >= 50, passed).else_(failed)
    [passed, failed] >> rep >> END
```

The scheduler evaluates each condition in order — these are `Ref` chains
with transforms like `eq` / `ge` — and fires only the matching op. The
others are skipped, and the fan-in into `rep` is softened at build time so
it runs on whichever arm completed.

Two things this form does that the older one did not:

- **Op instances instead of names.** `if_(cond, "handler")` still works
  for a forward reference, but a string cannot be renamed safely — the
  branch would route to a name that no longer exists.
- **The `branch >> target` wiring is derived.** You named the target once,
  inside `if_`; repeating it in a `route >> [a, b]` line was the second
  place to get it wrong.

Chain more arms with `.if_(...)` before `.else_(...)`:

```python
START >> cls >> (
    if_(cls["grade"] == "excellent", exc)
    .if_(cls["grade"] == "good", good)
    .else_(fail)
)
```

**The merge reads a cell, not the branch.** A soft fan-in decides *when*
`rep` runs, not what it receives — so both arms write `PARENT["label"]`
and `rep` reads that. Binding `rep` to `passed["label"]` would leave it
without input on the turns where `failed` ran.

## Putting it together

These pieces compose freely — a typical production graph looks like:

```python
@graph
def verify(score):
    cls = classify(score=score)
    pass_op = process_grade(grade=cls["grade"], score=cls["score"], name="pass_op")
    fail_op = process_grade(grade=cls["grade"], score=cls["score"], name="fail_op")
    out = collect(x=cls["score"])

    START >> cls >> if_(cls["score"] >= 50, pass_op).else_(fail_op)
    [pass_op, fail_op] >> out >> END

with GraphOp(name="batch") as main:
    cases = [verify(score=PARENT[f"score_{i}"], name=f"case{i}") for i in range(3)]
    agg = combine_all(r1=cases[0]["x"], r2=cases[1]["x"], r3=cases[2]["x"])
    for c in cases:
        START >> c >> agg
    agg >> END
```

`@graph`, edges, `if_()` routing, auto-softened merges, and PARENT/sibling
state references — together they cover the bulk of real workflows.
The rest of the guide drills into specific scenarios:

- [LLM chat](02-llm-chat.md) — `LLMOp.of()`, `prompt=` vs `messages=`, structured output.
- [Loops and branches](03-loops-and-branches.md) — back-edge loops, generator ops, `if_()` routing.
- [RAG](04-rag.md) — `EmbeddingOp` + retrieval + `RerankOp`.
- [Agents](05-agents.md) — tool-calling on `@graph.loop`.
- [Streaming](06-streaming.md) — `Ref.parallel()` / `.collect()`, real-time delivery.
- [Tracing](07-tracing.md) — `LangfuseTracer`, `OTELTracer`, the local file tracer.
