# Failure modes

Every entry here was paid for. These are not hypotheses — each one is a
defect that shipped, survived review, and was found later by measurement.
The bugs themselves are fixed and recorded in the
[CHANGELOG](https://github.com/batman1m2001-cyber/operonx/blob/main/CHANGELOG.md);
what is worth keeping is the *shape*, because the shapes recur.

The rule that generated this page: **state the measurement, not the
impression.** "Cancellation was too broad" is an opinion. "2 of 8 branches
survived before, 7 after" is a fact that stops the next person
re-litigating it.

---

## 1 · Silence is the failure mode

Every defect in the §16 sweep — seven of them — returned a *plausible
value* instead of an error.

| | What it returned |
|---|---|
| A run cancelled by a stray `Interrupt()` | `{"__interrupt__": …}`, no error, looked complete |
| A model answering with the wrong keys | `{"result": None, "error": None}` |
| A generator past its `observe_max` | 50 frames against a budget of 5 |
| A branch cancelled inside a subgraph | `None`, indistinguishable from a null answer |

None raised. None logged. Every one of them produced output a caller would
accept. **A defect that raises is cheap; a defect that answers is
expensive.** When adding a code path that can fail, ask what the caller
receives when it does, and make that distinguishable from success.

## 2 · A test can lock a bug in

`test_missing_field_becomes_none` asserted that a missing field returns
`None` with `error=None`. It passed for as long as it existed, and it was
asserting the bug. Same for A7, where a test asserted that compaction
declines to act — written from a small conversation, where the assertion
looks right, and locking in a compactor that refused at 114× over budget.

A test written *from the implementation* cannot fail. Ask instead: what
would a caller be entitled to expect? Write that.

## 3 · Scripted doubles verify code, never contracts

Four `§2` rows of the agent plan were wrong about how operonx behaves.
Every unit test passed. Four more defects hid until a live LLM run, and
seven more until adversarial review.

A mock returns what you told it to return, so a test built on one proves
your code is self-consistent — not that your assumption about the
dependency was right. Contracts need the real thing at least once.

## 4 · A fix can pass its own test and be wrong on most paths

`Interrupt.SELF` was verified against a batch op on a parallel branch: 2
survivors before, 7 after. Shipped. It was correct on **2 of 5 paths**.

| Path | Status after the "fix" |
|---|---|
| batch op on a parallel branch | correct — the one that was tested |
| inline `bound="sync"` op | correct |
| generator, mid-stream | swept its own siblings |
| nested `GraphOp` | unreported, plus a phantom `None` result |
| synthetic loop | inherited the nested-graph bug |

The test and the fix shared an assumption, so the test could not fail.
**When a mechanism has several dispatch paths, enumerate them before
declaring it fixed** — and the enumeration has to come from the code, not
from memory.

## 5 · Built, tested, exported, and wired to nothing

`plan_compaction`, `assemble_api_messages`, `apply_cache_control`,
`inject_skills` and `merge_memory` each had passing tests. None of them
was ever called. A deployment grew context until the provider rejected it
with a fully-tested compactor sitting unused.

Unit tests on each piece cannot catch this, because each piece worked. The
question that catches it is **"what does the far end actually receive?"** —
which is what `test_context_wiring.py` asks, and why it exists.

## 6 · A shared cell is shared — that is what it is for

Approval decisions travelled through a `PARENT` cell. With tool calls
fanned out, the last branch to answer overwrote its siblings, so denying
one destructive call and approving another ran **both**.

Every test answered all approvals identically, so the shared write was
invisible. `PARENT` is for values that *should* merge across contexts. A
per-branch decision is not one of them — it belongs in separate inputs.

## 7 · The right default depends on what the author meant

"A missing field is an error" is correct for a schema describing one
response and wrong for a schema describing several. Callbot's `ahamove_hr`
extractor declares twelve fields and expects one — a *union schema*, where
absence is normal. Requiring all twelve would have failed every call.

The framework cannot infer which the author wrote, so it has to ask:
`"name?: type"` marks a field optional. **When a default would be right
half the time, that is a signal the information belongs in the API, not in
a heuristic.**

## 8 · An empty prefix matches everything

`Interrupt(ctx_to_cancel=())` swept the whole run because `()` is a prefix
of every context. The same reasoning applies to empty allowlists, empty
glob patterns, and zero-length keys.

The fix was a sentinel that is deliberately **not** a tuple, so a path that
forgets to resolve it raises `TypeError` instead of matching everything.
**Make the degenerate case loud, not permissive.**

## 9 · Report what was refuted

Three high-severity findings from adversarial review were **wrong**, and
acting on any of them would have introduced a bug:

| Claim | Reality |
|---|---|
| Shared-cell defaults bleed across runs | No — `add_messages` returns a new list |
| A nested `PARENT.declare()` makes a separate cell | No — the outer cell held both writes |
| `add_messages` mutates its `old` argument | No — `old` was unchanged |

A plausible report from a careful reviewer is still a hypothesis. Probe
before you patch, and **write down what you disproved** — otherwise the
next session re-discovers the same plausible-and-wrong idea.

---

## The checklist this reduces to

Before calling something fixed:

1. What does a caller receive when this fails? Is it distinguishable from success?
2. Did I enumerate the dispatch paths from the code, or from memory?
3. Does my test assert the contract, or restate the implementation?
4. Has the new code been called by the thing that is supposed to call it?
5. Did I measure the before-state, or infer it?
