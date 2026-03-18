# Rust ↔ Python Alignment TODO

All known misalignments between the Python and Rust backends, deduplicated and ranked by implementation priority.

---

## Tier 1 — Fix Now (breaks correctness / observability)

### 1. Graph ops missing timing & metrics in Rust traces
- **File:** `rust/hush-icore/src/ops/graph/graph_op.rs` → `run_nested_graph_async()`
- **Problem:** Nested graph ops don't call the `Op::run()` lifecycle — no `$start_time`, `$end_time`, `$duration_ms` stored. Graph ops are invisible in Rust traces.
- **Python ref:** `python/hush-icore/hush/core/ops/graph/graph_op.py` → `GraphOp.run()` (lines 421-489) wraps execution with full timing, logging, and `_store_metrics()`.
- **Fix:** Wrap `run_nested_graph_async()` with timing capture and store `$start_time`/`$end_time`/`$duration_ms` + logging, matching `run_with_core()` pattern for leaf ops.
- **Effort:** Small (~30 lines)

### 2. Generator ops missing timing & metrics in Rust traces
- **Files:** `rust/hush-icore/src/ops/graph/graph_op.rs` → `run_registry_generator()`, `run_builtin_generator_inline()`
- **Problem:** Generator ops have no timing or metrics stored. They're invisible in traces.
- **Python ref:** `python/hush-icore/hush/core/ops/graph/scheduler.py` → `task_generator()` (lines 170-223) captures `gen_start`, `gen_perf`, calls `_store_metrics()` and `_log()`.
- **Fix:** Add `Instant::now()` before/after execution, store `$start_time`/`$end_time`/`$duration_ms` under the generator op's full_name.
- **Effort:** Small (~30 lines)

### 3. Failed nested graph still propagates to successors
- **File:** `rust/hush-icore/src/ops/graph/graph_op.rs` → `spawn_graph_task()` (lines 417-428)
- **Problem:** On error, Rust stores `error` in state but sends `SchedulerEvent::Done` — successors still run with missing/stale inputs. Python's error in `GraphOp.run()` prevents `store_result()` so downstream ops see missing inputs and the graph effectively stops that branch.
- **Fix:** Either (a) introduce `SchedulerEvent::Error` that doesn't propagate, or (b) send `DonePending` on error instead of `Done`, or (c) store a poison pill that leaf ops check.
- **Effort:** Small (~20 lines), but needs design decision on error strategy

---

## Tier 2 — Fix Soon (structural misalignment, not broken today)

### ~~4. Timing metadata key prefix mismatch~~ — NOT AN ISSUE
- **Verified:** Rust stores `$start_time` in state (internal convention) but `TraceCollector` strips the `$` prefix when building `TraceNode`. Python stores `start_time` directly. Both backends produce identical trace output: `start_time`, `end_time`, `duration_ms`. No change needed.

### ~~5. PromptOp not handled in Rust hush-icore~~ — NOT AN ISSUE
- **Verified:** PromptOp already exists at `rust/hush-providers/src/ops/prompt.rs` with full implementation. Registered in `factory.rs:19` via `OpFactory`. Falls through to factory by design — same pattern as LLM/Embedding/Rerank ops. No change needed.

### 6. Op struct hierarchy: flat dispatch vs polymorphic trait
- **File:** `rust/hush-icore/src/ops/graph/graph_op.rs` → `dispatch_leaf_op()`
- **Problem:** Rust uses string matching on `op_type` to construct ephemeral op structs (`FuncOp::new(op)`, `BranchOp::new(op)`), then calls `.run()`. This is a manual vtable — each new op type requires editing `dispatch_leaf_op`. Python's class hierarchy handles this via polymorphism.
- **Fix:** Construct `Box<dyn Op>` during config parsing (not at dispatch time), store in `GraphConfig.ops` alongside `BaseOpConfig`. Then `dispatch_leaf_op` becomes just `op.run(ctx)`.
- **Effort:** Medium-large (touches config parsing + graph_op dispatch + all op constructors)
- **Note:** Not blocking anything today, but every new op type adds another match arm.

### 7. Provider ops late-bound via OpFactory vs first-class in Python
- **Files:** `rust/hush-icore/src/ops/op_trait.rs` (OpFactory), `rust/hush-icore/src/ops/graph/graph_op.rs` (dispatch)
- **Problem:** Python's LLMOp/EmbeddingOp/RerankOp are `BaseOp` subclasses sharing the full `run()` lifecycle. Rust provider ops are created at dispatch time via `OpFactory::create_op()` — they exist only for the duration of execution. They do go through the `Op::run()` lifecycle, but the factory indirection means hush-icore has zero knowledge of provider semantics.
- **Fix:** This is by design (hush-icore is pure, providers live in hush-serve/hush-providers). But consider moving provider op structs into hush-providers as proper `Op` implementors with shared config parsing, rather than the opaque `provider_config: Option<Value>`.
- **Effort:** Large (cross-crate refactor)

---

## Tier 3 — Fix When Needed (design debt, not affecting results)

### 8. Context representation: tuples vs dot-separated strings
- **Python:** `("main", "[0]", "[1]")` — tuples
- **Rust:** `"main.[0].[1]"` — dot-separated strings
- **Problem:** Leaf context detection differs. String `starts_with` could theoretically match `"main.[1]"` as prefix of `"main.[10]"`, though current index formatting makes this unlikely.
- **Fix:** Not needed unless you hit the prefix collision. If you do, use a delimiter that can't appear in indices (e.g., `"main/[0]/[1]"`).
- **Effort:** Small but wide-reaching (every context comparison in Rust)

### 9. Stream predecrements: runtime vs serialization-time
- **Python:** Computes predecrements at build time (`_build_predecrements`), applies them at runtime during yield handling.
- **Rust:** Uses pre-computed `stream_ready_counts` from serialized config — predecrements baked in at serialization time.
- **Problem:** Not wrong, but the contract is implicit: Python's `serialize()` must produce correct `stream_ready_counts`. If serialization has a bug, Rust silently gets wrong ready counts.
- **Fix:** Add validation in Rust config parsing, or compute predecrements in Rust from `initial_ready_count` + `compiled_adj` (matching Python's runtime approach).
- **Effort:** Medium

### 10. Output collection divergence
- **Python:** Simple — 3 branches in scheduler (streaming, generators-no-yields, batch).
- **Rust:** Complex `get_outputs()` with 4 branches (explicit±streaming, auto-forward±streaming) plus fallback terminal-op search.
- **Problem:** Extra complexity in Rust could produce different results for edge cases. Python's `store_result()` writes to graph state so the simple path works; Rust's defensive fallbacks suggest that path sometimes doesn't.
- **Fix:** Investigate whether the Rust fallbacks are needed. If `push_output_refs` works correctly, the simple Python-style logic should suffice.
- **Effort:** Medium (requires test cases to verify)

### 11. Eager generators (Vec<Value>) vs lazy (yield)
- **Rust:** `OpRegistry::call_generator()` returns `Vec<Value>` — all items materialized before any downstream op runs.
- **Python:** Real `yield` — downstream ops start after each yield.
- **Problem:** Throughput difference for I/O-bound generators (API pagination, file streaming). No correctness impact — final results are identical.
- **Fix:** Change `call_generator` to return an iterator or use a channel. Natural time to fix: when implementing provider streaming (#12).
- **Effort:** Medium (API change in OpRegistry trait)

### 12. Provider streaming completely unimplemented in Rust
- **File:** `rust/hush-icore/src/ops/graph/graph_op.rs` → `run_provider_streaming()` (lines 695-731)
- **Problem:** Logs error and returns. Dead code kept "for reference." LLM streaming workflows can't run on Rust backend.
- **Fix:** Implement channel-based streaming via `OpFactory` extension (e.g., `create_streaming_op()` returning a `Receiver<Value>`). Tackle together with #11 (lazy generators).
- **Effort:** Large

### 13. Loop iteration context naming
- **Python vs Rust:** May differ in numbering (`loop_0` vs `loop_1`). Cosmetic — doesn't affect correctness.
- **Fix:** Align numbering convention if trace viewers need to correlate Python and Rust loops.
- **Effort:** Trivial

### 14. OpType coverage gap
- **Python:** Defines many types — `"data"`, `"mcp"`, `"tool-executor"`, `"milvus"`, `"mongo"`, `"s3"`, `"doc-processor"`, etc.
- **Rust:** Only handles `"code"`, `"parser"`, `"branch"`, `"graph"` natively. Everything else → OpFactory.
- **Problem:** Not wrong (factory is the extension point), but means Rust can't run *any* workflow with these types without hush-serve providing the factory.
- **Fix:** No action needed unless you want standalone hush-icore to handle more types. The factory design is intentional.
- **Effort:** N/A

---

## Implementation Order

```
Phase 1 (DONE):        #1, #2, #3          — trace completeness + error safety
Phase 2 (DONE):        #4, #5             — verified: not actual issues
Phase 3 (next):        #6, #7, #10        — structural cleanup
Phase 4 (with streaming): #11, #12        — lazy generators + provider streaming
Backlog:               #8, #9, #13, #14   — low-priority / cosmetic
```
