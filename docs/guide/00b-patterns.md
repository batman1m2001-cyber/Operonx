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
llm = LLMOp.of(resource="gpt-4o", prompt=PARENT["msgs"])
embed = EmbeddingOp.of(resource="bge-m3", texts=PARENT["texts"])

# Prompt template + LLM in one op — prompt accepts str / dict / list
c = LLMOp.of(
    resource="gpt-4o",
    prompt={"system": "...", "user": "{q}"},
    q=PARENT["q"],
)
```

Never positional. The keyword form catches typos at construction time
rather than at runtime against a wrong parameter name.

## Edge types — `>>` vs `>>~`

```python
START >> classify >> route             # hard edge: route waits for classify
route >> ~handler_a                    # soft edge: handler_a fires only if route picks it
route >> ~handler_b                    # soft edge: handler_b fires only if route picks it
[handler_a, handler_b] >> ~merge       # soft fan-in: merge accepts whichever fired
```

- **Hard `>>`** — sequential dependency. The destination's
  `ready_count` increments by one per hard predecessor; the op only
  fires after every hard predecessor has completed.
- **Soft `>>~`** — conditional dependency. Used for branch outputs
  and fan-in after a route. One soft predecessor unblocks the
  destination.

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

### `@graph.loop()` (replaces `WhileOp`)

A feedback loop that re-dispatches the inner graph until a Python
expression on the loop's state evaluates truthy:

```python
from operonx.core import GraphOp, START, END, PARENT

@op
def increment(counter: int):
    return {"counter": counter + 1}

with GraphOp.loop(until="count >= 5", count=0) as loop:
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]                   # update loop state
    START >> inc >> END
```

The decorator form, for reusable loops:

```python
from operonx.core import graph

@graph.loop(until="done == True", max_iterations=10)
def agent_loop(messages, done, answer):
    # …op definitions…
    process["new_messages"] >> PARENT["messages"]
    process["done"] >> PARENT["done"]
    process["answer"] >> PARENT["answer"]
```

The body's `>> PARENT[...]` lines are how the next iteration sees
updated state.

## Branch routing — `if_()` and `Branch`

Conditional dispatch through the scheduler:

```python
from operonx.core.ops.flow.branch_op import if_

with GraphOp(name="grader") as g:
    cls = classify(score=PARENT["score"])
    router = (
        if_(cls["grade"] == "excellent", "exc")
        .if_(cls["grade"] == "good",      "good")
        .if_(cls["grade"] == "average",   "avg")
        .else_("fail")
    )
    exc  = process_grade(grade=cls["grade"], score=cls["score"], name="exc")
    good = process_grade(grade=cls["grade"], score=cls["score"], name="good")
    avg  = process_grade(grade=cls["grade"], score=cls["score"], name="avg")
    fail = process_grade(grade=cls["grade"], score=cls["score"], name="fail")
    merge = collect(x=cls["score"])

    START >> cls >> router
    router >> [exc, good, avg, fail]
    [exc, good, avg, fail] >> ~merge                    # soft fan-in
    merge >> END
```

The `if_(condition, target)` chain produces a `Branch` op. At runtime
the scheduler evaluates each condition (these are `Ref` chains with
transforms like `eq` / `ge`) and fires only the matching downstream
op; the others are skipped. The trailing soft-edge merge picks
whichever branch fired.

For multiple `Branch` ops in the same graph, give each one an
explicit `name=` to prevent auto-naming collisions:

```python
from operonx.core.ops.flow.branch_op import Branch

router = (
    Branch(name="router0")
    .if_(cls["grade"] == "excellent", "exc")
    .else_("fail")
)
```

## Putting it together

These pieces compose freely — a typical production graph looks like:

```python
@graph
def verify(score):
    cls = classify(score=score)
    router = if_(cls["score"] >= 50, "pass_op").else_("fail_op")
    pass_op = process_grade(grade=cls["grade"], score=cls["score"], name="pass_op")
    fail_op = process_grade(grade=cls["grade"], score=cls["score"], name="fail_op")
    out = collect(x=cls["score"])

    START >> cls >> router
    router >> [pass_op, fail_op]
    [pass_op, fail_op] >> ~out
    out >> END

with GraphOp(name="batch") as main:
    cases = [verify(score=PARENT[f"score_{i}"], name=f"case{i}") for i in range(3)]
    agg = combine_all(r1=cases[0]["x"], r2=cases[1]["x"], r3=cases[2]["x"])
    for c in cases:
        START >> c >> agg
    agg >> END
```

`@graph`, hard edges, `if_()` routing, `>>~` merges, and PARENT/sibling
state references — together they cover the bulk of real workflows.
The rest of the guide drills into specific scenarios:

- [LLM chat](02-llm-chat.md) — `LLMOp.of()`, `prompt=` template shapes.
- [Loops and branches](03-loops-and-branches.md) — `@graph.loop`, generator ops, `if_()` routing.
- [RAG](04-rag.md) — `EmbeddingOp` + retrieval + `RerankOp`.
- [Agents](05-agents.md) — tool-calling on `@graph.loop`.
- [Streaming](06-streaming.md) — `Ref.parallel()` / `.collect()`, real-time delivery.
- [Tracing](07-tracing.md) — `LangfuseTracer`, `OTELTracer`, the local file tracer.
