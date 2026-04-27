# Rust Backend Migration Plan — `operonx`

**Source:** `Hush-ai/rust/{hush-icore, hush-providers, hush-telemetry}/src/`
**Target:** `operonx` — **single unified Rust crate** whose `src/` tree mirrors the Python `operonx/{core, providers, telemetry}/` tree file-for-file.
**Scope (strict):** **core, providers, telemetry only.** Everything else — `hush-serve`, `hush-plugin`, `ui-hush-eyes`, future `operonx-serve`, future trace viz — is **out of scope**. Do not port, do not plan, do not reference.
**Distribution:** `pip install operonx` / `cargo install operonx`. Python import stays `from operonx import ...`.

---

## 1. Guiding principles — full parity with Python

The Rust side must mirror the Python side at **every** level. A developer fluent in `operonx` (Python) should be able to navigate `operonx` (Rust) without a map.

1. **Folder structure parity** — `operonx/core/ops/graph/task_scheduler.py` ↔ `operonx/src/core/ops/graph/task_scheduler.rs`. No flattening.
2. **Module/file name parity** — one-to-one. Rust file = Python file with `.rs` instead of `.py`.
3. **Type name parity** — Python class `GraphOp` ↔ Rust struct `GraphOp`. Python `MemoryState` ↔ Rust `MemoryState`. Not `EngineState`. Not `Hush`. Not `LlmProvider`. Whatever Python calls it, Rust calls it the same.
4. **Method name parity** — Python `graph.serialize()` ↔ Rust `graph.serialize()`. Python `tracer.flush()` ↔ Rust `tracer.flush()`. Snake_case on both sides; dunder methods (`__getitem__`, `__rshift__`) map to Rust trait impls (`std::ops::Index`, `std::ops::Shr`) or named methods (`get_item`) when no operator trait fits.
5. **OOP hierarchy parity via trait+struct** — every Python base class becomes a Rust trait; every Python subclass becomes a Rust struct that `impl`s the trait. Shared state that lived in the Python base's `__init__` becomes a `Meta` helper struct embedded in each concrete struct. See §3a.
6. **Rust files that diverge from Python structure must be moved** — `hush-icore/src/config.rs` (multi-concern), `hush-telemetry/src/langfuse/client.rs` (missing `backends/` parent), flat `logging.rs`, etc. Relocate to match.

---

## 2. Crate layout

```
Operon/rust/
├── Cargo.toml                       # workspace root
├── operonx/
│   ├── Cargo.toml
│   └── src/
│       ├── lib.rs                   # pub mod core; pub mod providers; pub mod telemetry;
│       ├── core/                    # ← mirrors operonx/core/
│       ├── providers/               # ← mirrors operonx/providers/
│       └── telemetry/               # ← mirrors operonx/telemetry/
└── operonx-macros/                  # proc-macros (Rust requires separate crate)
    ├── Cargo.toml                   # proc-macro = true
    └── src/lib.rs                   # #[op], #[resource], #[model]
                                     # (short names match Python's @op; users reserve the identifiers)
```

Feature flags:
```toml
[features]
default      = ["langfuse", "operonx-eyes"]
operonx-eyes  = []
langfuse     = []
otel         = ["dep:opentelemetry", "dep:opentelemetry-otlp"]
onnx         = ["dep:ort", "dep:tokenizers"]
triton       = ["dep:tonic", "dep:prost"]
```

---

## 3. Classification legend

Throughout the tables below:

- ✅ **Reuse as-is** — port with renames only (`Hush → Operon`, `hush_op → op`, `hush_resource → resource`, `hush_model → model`, plus Python-parity struct/trait renames per §3a). Semantics unchanged.
- 🔧 **Reuse + modify** — current Rust logic is correct but needs adjustment to match current Python behavior (new fields, new methods, moved location, renamed to Python name).
- 🆕 **New work** — no Rust precedent in Hush-ai; must be written from scratch (or extracted from Python).
- ❌ **Drop** — Python authoring-layer concept that doesn't exist at runtime (build-time only; Rust consumes serialized graphs).

---

## 3a. Naming & OOP conventions

### 3a.1 Top-level engine type

| Python | Rust | Rationale |
|---|---|---|
| `class Operon` (in `operonx.core.engine`) | `struct Operon` (in `operonx::core::engine`) | Same class name. Crate name `operonx` only exists because PyPI `operonx` is squatted — the *type* name mirrors Python. |
| `class ExecutionHandle` | `struct ExecutionHandle` | — |
| `class Middleware` | `trait Middleware` | Abstract Python base → Rust trait. |

User code:
```rust
use operonx::Operon;                         // ← reads like from operonx import Operon
let engine = Operon::new(&json)?;
let result = engine.run(inputs).await?;
```

### 3a.2 How Python class hierarchy maps to Rust trait + struct

Python uses inheritance; Rust doesn't have it. The mapping is mechanical:

```
Python:                                Rust:
class BaseOp:                          pub trait BaseOp: Send + Sync {
    def __init__(self, name, ...)          fn meta(&self) -> &OpMeta;
    def run(self, state, ctx)              fn run(&self, ...) -> ... { /* default */ }
    def _exec_core(self, inputs)           fn exec_core(&self, ...) -> ...;
    def serialize(self)                    fn serialize(&self) -> Value { /* default */ }
                                       }

class FuncOp(BaseOp):                  pub struct FuncOp {
    def __init__(self, code_fn, ...)       pub meta: OpMeta,
        super().__init__(...)              pub code_fn: Arc<dyn Fn(...)>,
        self.code_fn = code_fn         }
    def _exec_core(self, inputs):      impl BaseOp for FuncOp {
        return self.code_fn(inputs)        fn meta(&self) -> &OpMeta { &self.meta }
                                           fn exec_core(&self, inputs: &Value, ...) -> ... {
                                               (self.code_fn)(inputs)
                                           }
                                       }
```

Three rules:

1. **Abstract base class → trait.** `class Tracer`, `class BaseOp`, `class BaseLLM`, `class BaseEmbedder`, `class BaseReranker`, `class ConfigStorage`, `class Middleware` — all become traits with the same name.
2. **Shared `__init__` state → `Meta` helper struct, embedded.** Python's `BaseOp.__init__` sets `name, full_name, inputs, outputs, stream, cache, delay, enabled, parent`. In Rust, these become a single `OpMeta` struct, and each concrete op struct (`FuncOp`, `GraphOp`, `BranchOp`, `ParserOp`, `LLMOp`, etc.) holds `pub meta: OpMeta` as its first field. The trait exposes `fn meta(&self) -> &OpMeta` so default methods on the trait can read shared state.
3. **Subclass → struct that `impl`s the trait.** The struct name mirrors Python exactly (`FuncOp`, not `FuncOpImpl`). `super().__init__(...)` in Python becomes constructing `OpMeta` and assigning it to `self.meta` in Rust's `::new()`.

The same pattern applies twice-over for nested hierarchies:

```
Python:                                Rust:
class Tracer: ...                      pub trait Tracer: Send + Sync { ... }
class ConfigurableTracer(Tracer):      pub trait ConfigurableTracer: Tracer {
    def _make_client(...)                  type Client;
    def _get_client(...)                   type Config: DeserializeOwned;
                                           fn make_client(...) -> Self::Client;
                                           fn get_client(&self) -> &Self::Client;
                                       }
class LangfuseTracer(ConfigurableTracer):  pub struct LangfuseTracer { ... }
                                       impl Tracer for LangfuseTracer { ... }
                                       impl ConfigurableTracer for LangfuseTracer {
                                           type Client = LangfuseClient;
                                           type Config = LangfuseConfig;
                                           ...
                                       }
```

### 3a.3 Method name conventions

| Python | Rust | Notes |
|---|---|---|
| `def serialize(self) -> dict` | `fn serialize(&self) -> serde_json::Value` | Same name. |
| `def run(self, state, ctx)` | `fn run(&self, state: &mut MemoryState, ctx: &OpContext)` | Same name. |
| `def _exec_core(self, inputs)` | `fn exec_core(&self, inputs: &Value, ...)` | Drop leading underscore (Rust uses `pub(crate)` or trait visibility). |
| `def __init__(self, ...)` | `fn new(...) -> Self` | Standard Rust constructor idiom. |
| `def __getitem__(self, key)` | `impl Index<K> for T` OR `fn get(&self, key: K)` | Use `Index` when key type is fixed and panic-on-miss is acceptable; otherwise named method. |
| `def __rshift__(self, other)` | `impl Shr<Rhs> for T` | For `>>` edge syntax. |
| `def __aiter__ / __anext__` | `impl Stream for T` | Via `futures::Stream`. |
| `@property def inputs(self)` | `fn inputs(&self) -> &HashMap<...>` | Named getter. |
| Private `_private_helper` | `fn private_helper` (module-private via `pub(crate)` or not `pub` at all) | — |
| `def flush(self, trace_data)` (sync, runs in bg thread) | `fn flush(&self, trace_data: &TraceData) -> Result<(), E>` (**sync**, dispatched on blocking thread pool) | See §4b.3 — not `async`. |

### 3a.4 Specific type renames from current Hush-ai Rust

Every one of these must be renamed to match Python:

| Hush-ai Rust (current) | Operon Rust (target) | Python equivalent |
|---|---|---|
| `struct Hush` | `struct Operon` | `class Operon` |
| `enum RushError` | `enum OperonError` (top-level) + `enum OpError` (op-level hierarchy) | `class OpError` + subclasses |
| `struct EngineState` | `struct MemoryState` | `class MemoryState` |
| `trait Op` | `trait BaseOp` | `class BaseOp` |
| `struct OpContext` | `struct OpContext` ✓ already matches | — |
| `trait OpRegistry` | `trait OpRegistry` ✓ already matches | — |
| `trait LlmProvider` | `trait BaseLLM` | `class BaseLLM` |
| `struct LlmOp` | `struct LLMOp` | `class LLMOp` |
| `trait EmbeddingProvider` | `trait BaseEmbedder` | `class BaseEmbedder` |
| `struct EmbeddingOp` | `struct EmbeddingOp` ✓ matches | — |
| `trait RerankingProvider` | `trait BaseReranker` | `class BaseReranker` |
| `struct RerankOp` | `struct RerankOp` ✓ matches | — |
| `struct LangfuseTracer` | `struct LangfuseTracer` ✓ matches | — |
| `struct HushEyesTracer` | `struct OperonEyesTracer` | `class OperonEyesTracer` |
| `struct OtelTracer` | `struct OTELTracer` | `class OTELTracer` (Python uses uppercase OTEL) |
| `#[hush_op]` | `#[op]` | `@op` |
| `#[hush_resource]` | `#[resource]` | — |
| `#[hush_model]` | `#[model]` | (`@dataclass` analog) |

### 3a.5 Config / dataclass parity

Python `@dataclass` classes → Rust `#[derive(Debug, Clone, Serialize, Deserialize)] struct` with `pub` fields and same field names:

```python
@dataclass
class OpenAIConfig(LLMConfig):
    api_key: str
    model: str
    base_url: Optional[str] = None
```

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OpenAIConfig {
    pub api_key: String,
    pub model: String,
    pub base_url: Option<String>,
}
```

Python's `LLMConfig` as parent is a union of provider configs — in Rust, model as an enum:

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "api_type", rename_all = "snake_case")]
pub enum LLMConfig {
    OpenAI(OpenAIConfig),
    Azure(AzureConfig),
    Gemini(GeminiConfig),
    Anthropic(AnthropicConfig),
}
```

### 3a.6 Error hierarchy parity

Python:
```python
class OpError(Exception): ...
class ParserError(OpError): ...
class CodeError(OpError): ...
class BranchError(OpError): ...
class ConditionError(OpError): ...
class IterationError(OpError): ...
class PromptError(OpError): ...
class EmbeddingError(OpError): ...
class RerankError(OpError): ...
```

Rust:
```rust
#[derive(thiserror::Error, Debug)]
pub enum OpError {
    #[error("parser error: {0}")]  Parser(String),
    #[error("code error: {0}")]    Code(String),
    #[error("branch error: {0}")]  Branch(String),
    #[error("condition error: {0}")] Condition(String),
    #[error("iteration error: {0}")] Iteration(String),
    #[error("prompt error: {0}")]  Prompt(String),
    #[error("embedding error: {0}")] Embedding(String),
    #[error("rerank error: {0}")]  Rerank(String),
}

#[derive(thiserror::Error, Debug)]
pub enum OperonError {
    #[error(transparent)] Op(#[from] OpError),
    #[error("provider: {0}")] Provider(String),
    #[error("resource hub: {0}")] ResourceHub(String),
    #[error("config: {0}")] Config(String),
    #[error("state: {0}")] State(String),
    #[error("runtime: {0}")] Runtime(String),
}
```

Rust idiom is enum variants (not separate subclasses) — semantically equivalent to Python's hierarchy for `isinstance`-style checks via `matches!` / pattern matching.

### 3a.7 Folder-level: `__init__.py` → `mod.rs`

Every Python `__init__.py` with its `__all__` list maps to a Rust `mod.rs` that declares submodules and re-exports with `pub use`:

```python
# operonx/core/states/__init__.py
from .state import MemoryState
from .ref import Ref, StreamPolicy
from .schema import StateSchema
from .cell import Cell

__all__ = ["MemoryState", "Ref", "StreamPolicy", "StateSchema", "Cell"]
```

```rust
// operonx/src/core/states/mod.rs
pub mod state;
pub mod ref_;    // note: `ref` is reserved in Rust, so file stays ref.rs, module is `ref_`
pub mod schema;
pub mod cell;

pub use state::MemoryState;
pub use ref_::{Ref, StreamPolicy};
pub use schema::StateSchema;
pub use cell::Cell;
```

**Rust keyword collisions** — a few Python names conflict with Rust reserved words:
- `ref` → module file stays `ref.rs`, module identifier `ref_` (with trailing underscore), type name `Ref` unchanged.
- `match` → not used in our Python code, no collision.
- `type` → not used as a file name in our Python code; field name `type:` on `BaseOp` maps to `kind:` in Rust (avoid collision with Rust's `type` keyword).

These are the **only** compromises. Everywhere else, names are identical.

---

## 3b. Async primitive translation (Python asyncio → Rust tokio)

Python is async-first with asyncio; Rust is async-first with tokio. Every async construct in Python has a tokio equivalent — use these mappings consistently throughout the port, don't invent ad-hoc alternatives.

```
Python asyncio                    →  Rust tokio
─────────────────────────────────────────────────────────────────────────────
asyncio.Queue(maxsize=N)          →  tokio::sync::mpsc::channel(N)
asyncio.Semaphore(n)              →  tokio::sync::Semaphore::new(n)
asyncio.Lock()                    →  tokio::sync::Mutex
asyncio.Event()                   →  tokio::sync::Notify
asyncio.create_task(coro)         →  tokio::spawn(future)
asyncio.to_thread(fn, ...)        →  tokio::task::spawn_blocking(fn)
asyncio.gather(*futures)          →  futures::future::join_all(vec)
asyncio.wait_for(fut, timeout)    →  tokio::time::timeout(duration, fut)
asyncio.CancelledError            →  tokio_util::sync::CancellationToken
async for x in stream             →  while let Some(x) = stream.next().await
contextvars.ContextVar            →  tokio::task_local!
async-generator                   →  async_stream::stream! { yield ...; }
```

**Rule: every channel is bounded.** No `unbounded_channel()` anywhere. Pick a capacity based on use case:

| Channel use | Capacity |
|---|---|
| Scheduler event queue (Frame/EOF) | 256 |
| `ExecutionHandle` frame stream | 128 |
| LLM streaming chunks (per request) | 64 |
| `FlushWorker` trace queue | 256 |
| Per-op output queue | 16 |

Oversized capacities wasted memory; undersized caps throughput. When in doubt, start at 64 and raise only if load tests show backpressure-induced stalls.

**Rule: bridge sync ↔ async only at the edges.**
- `Tracer::flush()` is sync (runs on `spawn_blocking` pool) — never `.await` inside it; use `reqwest::blocking::Client`.
- `FuncOp` with `bound="sync"` runs inline on the scheduler thread — no `.await`.
- `bound="cpu"` ops run on `rayon::spawn` — no tokio at all inside.
- Everything else is fully async.

---

## 4. `operonx/src/core/` — file-by-file mapping

```
operonx/core/ (Python)                         →  operonx/src/core/ (Rust)                    Class
──────────────────────────────────────────────────────────────────────────────────────────────────
__init__.py                                   →  mod.rs                                      🔧
engine.py  (Operon, ExecutionHandle)          →  engine.rs                                   🔧
exceptions.py (OpError hierarchy)             →  exceptions.rs                               🔧
middleware.py (Middleware)                    →  middleware.rs                               ✅
media.py (Media: url/base64/bytes)            →  media.rs                                    🆕

configs/__init__.py                           →  configs/mod.rs                              🔧
configs/edge_config.py (EdgeConfig, EdgeType) →  configs/edge_config.rs                      🔧
configs/op_config.py (OpType enum)            →  configs/op_config.rs                        🔧

ops/__init__.py                               →  ops/mod.rs                                  🔧
ops/base.py (BaseOp)                          →  ops/base.rs                                 🔧
ops/_edges.py (DummyOp, SoftEdge, START/END)  →  ops/edges.rs                                🆕
ops/_params.py (param normalize/merge)        →  ops/params.rs                               🆕
ops/_shortcuts.py (shorthand authoring)       →  (skip — authoring only)                     ❌
ops/_utils.py (wildcard outputs)              →  (skip — authoring only)                     ❌

ops/flow/branch_op.py (BranchOp)              →  ops/flow/branch_op.rs                       ✅

ops/transform/func_op.py (FuncOp + @op)       →  ops/transform/func_op.rs                    ✅
ops/transform/parser_op.py (ParserOp)         →  ops/transform/parser_op.rs                  ✅

ops/graph/graph_op.py (GraphOp)               →  ops/graph/graph_op.rs                       🔧
ops/graph/task_scheduler.py (Scheduler)       →  ops/graph/task_scheduler.rs                 🔧
ops/graph/_decorators.py (@graph)             →  (skip — authoring only)                     ❌
ops/graph/validation.py (validate_graph)      →  ops/graph/validation.rs                     🆕

states/__init__.py                            →  states/mod.rs                               🔧
states/state.py (MemoryState)                 →  states/state.rs                             🔧
states/ref.py (Ref, StreamPolicy)             →  states/ref.rs                               🔧
states/schema.py (StateSchema)                →  states/schema.rs                            🆕
states/cell.py (Cell)                         →  states/cell.rs                              🆕

registry/__init__.py                          →  registry/mod.rs                             🔧
registry/config_registry.py (ConfigRegistry)  →  registry/config_registry.rs                 🆕
registry/resource_hub.py (ResourceHub)        →  registry/resource_hub.rs                    🆕
registry/shortcuts/__init__.py                →  registry/shortcuts/mod.rs                   🆕
registry/shortcuts/health.py                  →  registry/shortcuts/health.rs                🆕
registry/storage/__init__.py                  →  registry/storage/mod.rs                     🆕
registry/storage/base.py (ConfigStorage)      →  registry/storage/base.rs                    🆕
registry/storage/yaml.py (env interpolation)  →  registry/storage/yaml.rs                    🆕
registry/storage/json.py                      →  registry/storage/json.rs                    🆕

tracing/__init__.py                           →  tracing/mod.rs                              🔧
tracing/base.py (Tracer)                      →  tracing/base.rs                             ✅
tracing/collector.py (TraceCollector)         →  tracing/collector.rs                        🔧
tracing/flush_worker.py (FlushWorker)         →  tracing/flush_worker.rs                     ✅
tracing/labels.py (label fn)                  →  tracing/labels.rs                           🆕
tracing/local.py (LocalTracer)                →  tracing/local.rs                            ✅
tracing/models.py (TraceNode, TraceSummary)   →  tracing/models.rs                           🔧
tracing/trace_filter.py (TraceFilter)         →  tracing/trace_filter.rs                     🆕

loggings/__init__.py                          →  loggings/mod.rs                             🔧
loggings/config.py                            →  loggings/config.rs                          🔧
loggings/events.py                            →  loggings/events.rs                          🔧
loggings/formatters.py                        →  loggings/formatters.rs                      🔧
loggings/theme.py                             →  loggings/theme.rs                           🔧
loggings/handlers/__init__.py                 →  loggings/handlers/mod.rs                    🔧
loggings/handlers/console.py                  →  loggings/handlers/console.rs                🔧
loggings/handlers/file.py                     →  loggings/handlers/file.rs                   🔧

utils/__init__.py                             →  utils/mod.rs                                🔧
utils/common.py (Param)                       →  utils/common.rs                             🔧
utils/context.py (_current_graph)             →  (skip — authoring only)                     ❌
utils/auto_name.py                            →  (skip — authoring only)                     ❌
utils/yaml_model.py (YamlModel)               →  utils/yaml_model.rs                         ✅
utils/bimap.py                                →  utils/bimap.rs                              🆕
utils/algo.py                                 →  utils/algo.rs                               🆕
```

### 4a. Detailed notes for `core/`

**engine.rs** 🔧
Rust has `hush-icore/src/engine.rs` with `Hush::new(json)`. Rewrite surface to match Python `Operon`:
- Constructor auto-loads `./.env` (via `dotenvy`, non-destructive) **and** `./resources.yaml` (required; error if missing; aggregated missing-env-var reporting).
- `run_json`, `run_json_async`, plus new `start() -> ExecutionHandle` streaming handle (Python `engine.start()`).
- `ExecutionHandle`: `Stream<Item = FrameEvent>`, `wait_for(op, var)`, `collect()`, `cancel()`. Currently Rust has LLM-only chunk streaming via `mpsc::Sender<String>` — needs full Frame/EOF streaming.
- Middleware chain invocation wrapping `before_run` / `after_run` / `on_error`.

**exceptions.rs** 🔧
Current `hush-icore/src/error.rs` is a single `RushError` enum. Split into Python's hierarchy:
```rust
pub enum OperonError {
    Op(OpError),           Parser(String),       Code(String),
    Branch(String),        Condition(String),    Iteration(String),
    Prompt(String),        Embedding(String),    Rerank(String),
    Provider { provider: String, source: Box<dyn Error + Send + Sync> },
    ResourceHub(String),   Config(String),       State(String),
    Runtime(String),
}
```

**middleware.rs** ✅
`hush-icore/src/middleware.rs` already defines the trait with `before_run / after_run / on_error`. Port as-is, hook into `engine.rs` run path (currently not wired).

**media.rs** 🆕
Python `core/media.py` defines `Media` with `from_url / from_base64 / from_data / to_bytes / is_image / mime_type`. No Rust equivalent. Write from scratch — used by multimodal LLM inputs and trace `media` field.

**configs/** 🔧
All three files currently live inlined inside `hush-icore/src/config.rs` (as `AdjEntry`, `BaseOpConfig`, `ParamConfig`, etc.). Extract into the nested `configs/` module to match Python. Update schema to cover: `compiled_adj`, `initial_ready_count`, `stream_initial_ready`, `stream_policies` **(new field — Python may add this during serialize)**, `loop_config: {until: Option<String>, max_iterations: Option<u32>}` *(no `loop_vars` — see §4b.2)*, `max_stream_concurrent`, `inputs/outputs: {default, required, ref, literal}`. `OpType` is an enum with `#[serde(rename_all = "kebab-case")]` covering 21 variants — see §4b.10.

**ops/base.rs** 🔧
`hush-icore` has `ops/base.rs` (`run_with_core` free fn) + `ops/op_trait.rs` (`Op` trait). Python's `BaseOp` is a class — map the trait to `ops::base::Op` (one file). Add:
- Middleware hook points.
- Timing metadata keys exactly matching Python: `$start_time`, `$end_time`, `$duration_ms`, `error` (Rust currently uses plain strings without the `$` prefix in some places — audit).

**ops/edges.rs** 🆕
`_edges.py` defines the Python-side `START / END / PARENT` sentinels + `DummyOp` + `SoftEdge`. At runtime these are serialized as special op names. Rust needs constants (`START_NAME`, `END_NAME`, `PARENT_NAME`) and adjacency-list recognition of these sentinels during graph resolution.

**ops/graph/graph_op.rs** 🔧
Currently `graph_op.rs` contains both the `GraphOp` struct **and** the full scheduler logic (~1500 lines). Split: `GraphOp` stays in `graph_op.rs`, scheduler moves to `task_scheduler.rs`.

**ops/graph/task_scheduler.rs** 🔧
Extract scheduler from `graph_op.rs`. Add:
- `StreamPolicy` enum: `Sequential` (default), `Parallel { max: Option<usize> }`, `Collect`.
- Per-input stream-policy dispatch: Sequential queues one item; Parallel dispatches all (capped by `max_stream_concurrent` semaphore); Collect buffers until source EOF then dispatches as single list.
- Frame/EOF event variants aligned with Python (`SchedulerEvent::{Frame, Eof, Yield, Exhausted}` → rename/align with Python's `Frame`/`EOF`).

**ops/graph/validation.rs** 🆕
Python has runtime-callable graph validation (cycles, unreachable ops, broken refs). Optional for Rust — add but gate behind a `#[cfg(feature = "validation")]` or always-on with cheap checks.

**states/state.rs** 🔧
Rust `EngineState` uses `DashMap<(Spur, Spur, Spur), Arc<Value>>` + lasso interning. Python `MemoryState` uses indexed `Cell`s via `StateSchema`. Keep Rust's DashMap for now (proven) but wrap as `MemoryState` with the same public surface: `__getitem__` ≡ `get`, `__setitem__` ≡ `store`, plus `request_id / user_id / session_id / tags / tracing` fields.

**states/ref.rs** 🔧
Rust has `refs/ref_transforms.rs` with the full transform set (getitem, arithmetic, boolean, etc.) — reuse this. Relocate to `core/states/ref.rs` to match Python. Add `StreamPolicy` struct (scheduler reads it).

**states/schema.rs** 🆕
Python `StateSchema` pre-computes op var indices at `build()` for O(1) state access and compiles Ref `_fn` closures once. Rust currently resolves refs dynamically. Add `StateSchema` with:
- `var_to_idx: HashMap<(String, String), usize>` — O(1) lookup.
- `pull_refs / push_refs` — compiled closures per op.
- `shared_indices` — slots for literal-input values shared across contexts.
- `stream_policies: HashMap<(String, String), StreamPolicy>` — scheduler input.

**states/cell.rs** 🆕
Python `Cell` is multi-context value storage with read cache + default fallback + `is_shared` flag. Wrap Rust's per-key DashMap entries in a `Cell` abstraction to expose the same API.

**registry/config_registry.rs** 🆕
Rust `registry.rs` currently has `OpRegistry` (op factories). Python also has `ConfigRegistry` (plugin registration of config classes). These are distinct concerns — add `ConfigRegistry` alongside existing `OpRegistry`.

**registry/resource_hub.rs** 🆕
**The most load-bearing new module.** Currently Rust ops read `provider_config` JSON inline. Port Python's singleton:
```rust
pub struct ResourceHub { /* DashMap<String, Arc<dyn Any>> */ }
impl ResourceHub {
    pub fn from_yaml(path: &Path) -> Result<Self, OperonError>;
    pub fn from_json(path: &Path) -> Result<Self, OperonError>;
    pub fn instance() -> Arc<Self>;                 // OnceLock singleton
    pub fn set_instance(hub: Arc<Self>);
    pub fn reset_instance();                        // for tests
    pub fn get<T: 'static>(&self, key: &str) -> Result<Arc<T>, OperonError>;
    pub fn get_config(&self, key: &str) -> Result<Value, OperonError>;
    pub fn health_check(&self) -> Vec<HealthCheckResult>;
}
```
Keys: `"llm:gpt-4o"`, `"embedding:bge-m3"`, `"rerank:cohere-r3"`, `"langfuse:default"`.

**registry/storage/yaml.rs** 🆕
`${VAR}` and `${VAR:default}` interpolation with consolidated missing-var error (collects all undefined refs before raising).

**tracing/models.rs** 🔧
Current `TraceNode` has timing only. Extend to Python's full shape:
- `kind: NodeKind` — `Batch | Generator | StreamContext | StreamItem | LoopIter | Graph`.
- `node_type: NodeType` — `Trace | Span | Generation`.
- `model: Option<String>`, `usage: Option<Usage>`, `cost: Option<f64>`.
- `media: Vec<MediaRef>`, `thinking_content: Option<String>`.
- `parent_trace_key: Option<String>` (pre-computed).

**tracing/collector.rs** 🔧
Rewrite to walk graph + state and emit one `TraceNode` per (op, ctx) — currently only emits at terminal ops.

**tracing/trace_filter.rs** 🆕
Port `TraceFilter` — selective op/var filtering (mirrors Python's `_trace_filter`).

**tracing/labels.rs** 🆕
Port `label()` runtime labeling fn for stream context iterations.

**loggings/** 🔧
Rust has a flat `logging.rs`. Split to match Python's `loggings/{config, events, formatters, theme, handlers/{console, file}}`. Translate any remaining Vietnamese templates to English.

**utils/** 🔧🆕❌
- `common.rs` (Param) — extract from `config.rs::ParamConfig`.
- `yaml_model.rs` — serde_yaml base — keep.
- `algo.rs` — **runtime-used** by `TraceCollector` (topo_rank) and graph validation. Port required.
- `bimap.rs` — no runtime callers found; defer unless discovered needed.
- `context.py`, `auto_name.py` — **drop** (Python authoring-layer, irrelevant to Rust runtime).

---

### 4b. Logic details resolved after source verification

Points where a naive port would silently diverge from Python semantics. Each was confirmed against specific Python source locations.

**4b.1 `graph.serialize()` is NOT JSON-safe — requires a cleanup pass.**
`BaseOp.serialize()` at [base.py:797-820](../../Operon/operonx/core/ops/base.py#L797) includes `python_callable: self.core` — a raw Python function object. Python backend uses it; Rust cannot.

Resolution:
- **Python side** adds `Operon.export_config(path: Path, pretty: bool = True) -> None` that walks the `graph.serialize()` dict, drops every `python_callable` key, injects `"schema_version": "1.0"` at the root, and writes JSON. This is the one user-facing entry point to produce a Rust-consumable config file.
- **Rust side** `GraphConfig` uses `#[serde(default)]` and does not deserialize `python_callable` — unknown-field rejection must *exclude* it so old configs still parse. Prefer `#[serde(skip)]` on the field in Rust-side mirror structs.

**4b.2 `loop_config.until` serialization — strings only.**
[graph_op.py:551-553](../../Operon/operonx/core/ops/graph/graph_op.py#L551) explicitly sets `until` to the string form or `None`; callable conditions are dropped at serialization time. Rust schema:
```rust
pub struct LoopConfig {
    pub until: Option<String>,
    pub max_iterations: Option<u32>,
}
```
**No `loop_vars` field in serialization** — earlier plan draft mentioned `loop_vars`, this is wrong. Loop state is carried forward via normal `inputs`, not a dedicated field.

Rust loop evaluator parses the `until` expression (e.g., `"count >= 5"`) via a small expression evaluator. `loop_eval.rs` in Hush-ai already supports this — reuse.

**4b.3 `Tracer.flush()` is sync, runs on a separate thread (not an async task).**
[base.py:55-64](../../Operon/operonx/core/tracing/base.py#L55) `flush()` is a plain `def`, not `async def`. `FlushWorker` uses `ThreadPoolExecutor` to invoke it off the main asyncio loop.

Rust trait:
```rust
pub trait Tracer: Send + Sync {
    fn flush(&self, trace_data: &TraceData) -> Result<(), OperonError>;
    fn to_config_dict(&self) -> Option<serde_json::Value> { None }
    fn tags(&self) -> &[String] { &[] }
    fn trace_filter(&self) -> Option<&TraceFilter> { None }
}
```
**Explicitly sync.** `FlushWorker` dispatches each `flush()` call via a dedicated OS thread (or `tokio::task::spawn_blocking` if already in an async context). Rust HTTP inside `flush()` uses `reqwest::blocking::Client` OR wraps an async call with `tokio::runtime::Handle::block_on` on the blocking thread. Either works — choose blocking client for simplicity.

**4b.4 Context type: tuple, not flat key.**
Python context is a tuple: `("main",)` by default, `("main", "[0]")` for a stream item, `("main", "[0]", "loop_1")` nested. Parent-walk on read means `("main", "[0]")` falls back to `("main",)` if the key isn't found at the leaf.

Hush-ai Rust currently uses a flat triple `(Spur, Spur, Spur)` — that **doesn't match Python's variable-length context**. The parent-walk is faked in Rust by comparing prefixes of fixed-arity tuples, which limits nesting depth.

Resolution — change Rust context to match Python:
```rust
pub type ContextId = SmallVec<[Spur; 4]>;     // variable-length, heap-spill after depth 4

// MemoryState lookup walks up by popping last element:
fn get(&self, op: Spur, var: Spur, ctx: &[Spur]) -> Option<Arc<Value>> {
    let mut ctx = ctx.to_vec();
    loop {
        if let Some(v) = self.cells.get(&(op, var, ctx.clone().into())) { return Some(v); }
        if ctx.is_empty() { return None; }
        ctx.pop();
    }
}
```
This is a small perf cost (a few extra hashmap lookups on miss) but preserves Python semantics exactly. Depth >4 is rare (deep loops), so the SmallVec heap-spill is acceptable.

**4b.5 Middleware execution order — inserted order forward, reversed for after/error.**
[engine.py:513-535](../../Operon/operonx/core/engine.py#L513) — `before_run` called in `self._middlewares` order; `after_run` and `on_error` iterate the **same list in reverse**. This is the classic onion/stack semantics.

Rust must match:
```rust
for mw in &self.middlewares           { mw.before_run(...).await?; }
// ... engine executes ...
for mw in self.middlewares.iter().rev() { mw.after_run(...).await?; }
for mw in self.middlewares.iter().rev() { mw.on_error(...).await;  }
```

**4b.6 Refs are 1-hop only — no transitive resolution.**
A Ref like `op_b["x"]` reads exactly one state cell (`op_b.x` in current context). It does **not** chase a chain (e.g., if `op_b.x` itself was set from `op_a.y`, the Ref reads the stored copy on `op_b`, not from `op_a`).

Rust `StateSchema` must encode this: each op's `pull_refs` point at exactly one source cell each. The scheduler's push step writes output values into the destination cell; downstream reads pick up the stored value, never re-resolving back through the source. This is the Python behavior and matches how the current Rust already works — keep as-is; do not "optimize" by chasing refs.

**4b.7 `ExecutionHandle` full surface to port.**
From [engine.py:131-230](../../Operon/operonx/core/engine.py#L131):
```python
async for op, ctx, data in handle:  # frame iteration
value = await handle["op_name", "var_name"]  # point query
result = await handle.collect(mode="group", unwrap=False)  # aggregate
result = handle.result(unwrap=True)  # non-consuming, safe after iter
handle.cancel()
```

Rust:
```rust
pub struct ExecutionHandle {
    frames_rx: tokio::sync::mpsc::Receiver<FrameEvent>,
    buffered: Arc<Mutex<Vec<Frame>>>,
    waiters: Arc<Mutex<HashMap<(String, String), oneshot::Sender<Value>>>>,
    cancel: CancellationToken,
    task: JoinHandle<Result<(), OperonError>>,
}

impl Stream for ExecutionHandle {                  // async for
    type Item = (OpName, ContextId, Value);
}
impl ExecutionHandle {
    pub async fn wait_for(&self, op: &str, var: &str) -> Result<Value, OperonError>;
    pub async fn collect(&mut self, mode: CollectMode, unwrap: bool) -> Result<Value, OperonError>;
    pub fn result(&self, unwrap: bool) -> Result<Value, OperonError>;  // non-consuming
    pub fn cancel(&self);
}

pub enum CollectMode { Group, Flat }
```

`wait_for` internally registers a `oneshot::Sender` keyed by `(op, var)`; the frame-processing loop fires matching waiters as frames arrive. `result()` reads the buffered `frames` without consuming the stream.

**4b.8 Collector synthesizes `stream_context` nodes.**
[collector.py](../../Operon/operonx/core/tracing/collector.py) groups `[0]`, `[1]`, ... stream items under a synthetic `TraceNode` with `kind = "stream_context"`. Rust `TraceCollector::collect()` must produce the same synthetic parent — not an emergent structure but an explicit group node inserted during tree build.

**4b.9 `MediaRef` companion to `Media`.**
Both are dataclasses. `MediaRef.from_media(media, field_path)` produces a reference for traces (media stripped from main payload, kept as referenceable side-channel). Rust needs both:
```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Media { pub data: MediaData, pub mime_type: String }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum MediaData { Bytes(Vec<u8>), Url(String) }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MediaRef { pub media: Media, pub field_path: String }

impl MediaRef {
    pub fn from_media(media: Media, field_path: String) -> Self { ... }
}
```

**4b.10 `OpType` is a `Literal` string, not an Enum — and the list is longer than earlier drafts.**
Full list from [op_config.py:5-34](../../Operon/operonx/core/configs/op_config.py#L5):
```
data, llm, embedding, rerank, branch, for, while, stream, code, lambda,
parser, prompt, doc-processor, milvus, mongo, s3, graph, default, dummy,
tool-executor, mcp
```
Rust `OpType` is an enum; variants use `#[serde(rename = "...")]` for the string and `-` → `_` naming:
```rust
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum OpType {
    Data, Llm, Embedding, Rerank, Branch,
    #[serde(rename = "for")]    ForLoop,
    #[serde(rename = "while")]  WhileLoop,
    Stream, Code,
    #[serde(rename = "lambda")] Lambda,
    Parser, Prompt, DocProcessor, Milvus, Mongo, S3, Graph,
    Default, Dummy, ToolExecutor, Mcp,
}
```
Collision with Rust reserved words (`for`, `while`) handled via explicit rename attributes.

**4b.11 `FuncOp.serialize()` extra keys — `is_async`, `is_generator`.**
Earlier plan draft missed these. The Rust side **needs them** because dispatch differs:
- `is_async=true, is_generator=false` → `tokio::spawn` (async fn) — matched by Rust `#[op] async fn`.
- `is_async=false, is_generator=false` → inline sync call — matched by regular `#[op] fn`.
- `is_generator=true` → `GeneratorOp` dispatch (yields multiple values).

Rust `FuncOpConfig`:
```rust
pub struct FuncOpConfig {
    pub func_name: String,
    pub is_async: bool,
    pub is_generator: bool,
    // python_callable explicitly not deserialized
}
```

Registry dispatch selects the right execution path from these flags.

**4b.12 `SchedulerEvent` — Python has Frame + EOF only; Rust currently has 4 variants.**
Python [task_scheduler.py](../../Operon/operonx/core/ops/graph/task_scheduler.py) emits just `Frame(op, ctx, result)` and `EOF(op, ctx)`. Hush-ai Rust has `SchedulerEvent::{Done, DonePending, Yield, Exhausted}` — different partition.

Resolution — pick the Python shape for parity:
```rust
pub enum SchedulerEvent {
    Frame { op: Spur, ctx: ContextId, result: Arc<Value> },
    Eof   { op: Spur, ctx: ContextId },
}
```
The PENDING / Yield / Exhausted distinctions collapse into `Frame { result: Arc<Value::Null> }` or are tracked separately in scheduler state, not as event variants.

**4b.13 Minor — `Operon.__call__()` alias for `run()`.**
[engine.py:539-551](../../Operon/operonx/core/engine.py#L539) lets `engine(inputs=...)` work. Rust has no clean mirror for `__call__` on a struct. **Skip** — Rust users write `engine.run(inputs).await?` explicitly; document this as one of the few Python ergonomic shortcuts that doesn't translate.

**4b.14 `LocalTracer` is a runtime debug tracer, not build-time.**
Writes traces to a JSON file after each run. Rust [tracing/local.rs](../../Hush-ai/rust/hush-icore/src/tracing/local.rs) already mirrors this.

**4b.15 `algo.py` IS used at runtime.**
`topo_rank` called from `TraceCollector` during trace build. Earlier plan draft marked `utils/algo.rs` as "optional / deferred." **Correct to: required port.**

---

## 5. `operonx/src/providers/` — file-by-file mapping

```
operonx/providers/ (Python)                    →  operonx/src/providers/ (Rust)              Class
──────────────────────────────────────────────────────────────────────────────────────────────────
__init__.py                                   →  mod.rs                                      🔧

llms/__init__.py                              →  llms/mod.rs                                 🔧
llms/base.py (BaseLLM)                        →  llms/base.rs                                ✅
llms/config.py (LLMConfig, OpenAI/Azure/...)  →  llms/config.rs                              ✅
llms/factory.py (create_llm)                  →  llms/factory.rs                             🔧
llms/response.py (LLMGenerator)               →  llms/response.rs                            🆕
llms/openai.py                                →  llms/openai.rs                              ✅
llms/azure.py                                 →  llms/azure.rs                               ✅
llms/anthropic.py                             →  llms/anthropic.rs                           ✅
llms/gemini.py                                →  llms/gemini.rs                              ✅
llms/batch_coordinator.py                     →  llms/batch_coordinator.rs                   🔧
                                                 (types.rs, image.rs kept Rust-internal)

embeddings/__init__.py                        →  embeddings/mod.rs                           🔧
embeddings/base.py                            →  embeddings/base.rs                          ✅
embeddings/config.py                          →  embeddings/config.rs                        ✅
embeddings/factory.py                         →  embeddings/factory.rs                       🔧
embeddings/vllm.py                            →  embeddings/vllm.rs                          🔧
embeddings/tei.py                             →  embeddings/tei.rs                           🆕
embeddings/huggingface.py                     →  embeddings/huggingface.rs                   🔧
embeddings/onnx.py                            →  embeddings/onnx.rs                          ✅
                                                 (openai.rs covers OpenAI/Azure natively)

rerankers/__init__.py                         →  rerankers/mod.rs                            🔧
rerankers/base.py                             →  rerankers/base.rs                           ✅
rerankers/config.py                           →  rerankers/config.rs                         ✅
rerankers/factory.py                          →  rerankers/factory.rs                        🔧
rerankers/vllm.py                             →  rerankers/vllm.rs                           ✅
rerankers/tei.py                              →  rerankers/tei.rs                            🆕
rerankers/huggingface.py                      →  rerankers/huggingface.rs                    🔧
rerankers/onnx.py                             →  rerankers/onnx.rs                           ✅
rerankers/pinecone.py                         →  rerankers/pinecone.rs                       ✅
                                                 (cohere.rs kept as Rust-internal extension)

auth/__init__.py                              →  auth/mod.rs                                 🔧
auth/config.py (KeycloakTokenConfig)          →  auth/config.rs                              🔧
auth/factory.py (create_auth)                 →  auth/factory.rs                             🔧
auth/keycloak.py (KeycloakTokenProvider)      →  auth/keycloak.rs                            ✅
                                                 (google.rs kept Rust-only, optional)

onnx/__init__.py                              →  onnx/mod.rs                                 🔧
onnx/config.py (OnnxInferenceConfig)          →  onnx/config.rs                              ✅
onnx/factory.py                               →  onnx/factory.rs                             🆕
onnx/backend.py (OnnxInferenceBackend)        →  onnx/backend.rs                             🆕

ops/__init__.py                               →  ops/mod.rs                                  🔧
ops/llm.py (LLMOp)                            →  ops/llm.rs                                  🔧
ops/embedding.py (EmbeddingOp)                →  ops/embedding.rs                            🔧
ops/rerank.py (RerankOp)                      →  ops/rerank.rs                               🔧
ops/onnx.py (OnnxOp)                          →  ops/onnx.rs                                 🔧
ops/triton.py (TritonOp)                      →  ops/triton.rs                               🆕
ops/prompt.py (PromptOp)                      →  ops/prompt.rs                               ✅
ops/chain.py (chat, ask — @graph builders)    →  (skip — Python authoring only)              ❌
ops/_utils.py (resolve_hub only)              →  ops/utils.rs                                🔧
(Rust-only)                                   →  ops/factory.rs                              ✅

registry/__init__.py                          →  registry/mod.rs                             🆕
registry/llm_plugin.py                        →  registry/llm_plugin.rs                      🆕
registry/embedding_plugin.py                  →  registry/embedding_plugin.rs                🆕
registry/rerank_plugin.py                     →  registry/rerank_plugin.rs                   🆕
registry/auth_plugin.py                       →  registry/auth_plugin.rs                     🆕
registry/onnx_plugin.py                       →  registry/onnx_plugin.rs                     🆕

_utils/__init__.py                            →  utils/mod.rs                                🔧
_utils/onnx.py                                →  utils/onnx.rs                               🔧
_utils/huggingface.py                         →  utils/huggingface.rs                        🔧

(no Python equivalent — Rust-internal shared)  →  http/mod.rs                                 ✅
                                                 (reqwest client singleton + ProviderError)
```

### 5a. Detailed notes for `providers/`

**Reusable as-is (✅):** Almost every backend implementation. The native HTTP providers (OpenAI, Azure, Gemini, Anthropic), ONNX embeddings/rerankers, Keycloak auth, and Pinecone reranker are complete in Rust and map 1:1 to Python backends. Rename, relocate to match Python tree, done.

**Key relocations (🔧):**
- `hush-providers/src/batch/coordinator.rs` → `providers/llms/batch_coordinator.rs` (Python locates it under `llms/`).
- `hush-providers/src/langfuse/*` (wait — that's telemetry, see §6).
- Split inline `ProviderConfig` enum in `config/mod.rs` into per-subtype files (llms/config.rs, embeddings/config.rs, rerankers/config.rs) matching Python.
- `factory.py` files: currently Rust uses inline dispatch in each `mod.rs`. Extract into named `factory.rs` files per Python structure.

**Load-bearing provider ops rewrites (🔧) — all in `ops/*.rs`:**
All five provider ops need the `ensure_initialized()` pattern (Python `_ensure_initialized`, Rust drops leading `_`):
```rust
pub struct LLMOp {
    pub meta: OpMeta,
    pub resource: OneOrMany<String>,
    pub batch_mode: bool,
    pub ratios: Vec<f32>,
    pub fallback: Vec<String>,
    llms: OnceLock<Vec<Arc<dyn BaseLLM>>>,        // plural — multi-model support
    batch_coordinator: OnceLock<Arc<BatchCoordinator>>,
    rng: Mutex<StdRng>,                           // seeded RNG for deterministic selection
    initialized: AtomicBool,
}

impl LLMOp {
    fn ensure_initialized(&self) -> Result<(), OperonError> {
        if self.initialized.load(Ordering::Acquire) { return Ok(()); }
        let hub = ResourceHub::instance();
        let llms: Vec<_> = self.resource.iter()
            .map(|k| hub.get::<dyn BaseLLM>(&format!("llm:{}", k)))
            .collect::<Result<_, _>>()?;
        self.llms.set(llms).ok();
        if self.batch_mode {
            self.batch_coordinator.set(BatchCoordinator::new(/* ... */)).ok();
        }
        self.initialized.store(true, Ordering::Release);
        Ok(())
    }
}
```
Called at `exec_core()` prelude. Same pattern for `EmbeddingOp`, `RerankOp`, `OnnxOp`, new `TritonOp`.

**`LLMOp` streaming (🔧):** Current Rust uses `std::sync::mpsc::Sender<String>` for LLM chunks — replace with emission into the new `ExecutionHandle`'s `FrameEvent` stream.

**TritonOp (🆕):** Full port from Python `providers/ops/triton.py`. Add `tonic` (gRPC) and `prost` (protobuf) as feature-gated deps. Triton HTTP path can reuse the shared `http/` reqwest client.

**Plugin registration (🆕):** Each Python plugin file auto-registers a `(config_class, factory)` pair into `ConfigRegistry` / `ResourceHub` on import. In Rust, implement via `inventory` submissions in each `registry/*_plugin.rs`:
```rust
// providers/registry/llm_plugin.rs
use operonx::resource;

inventory::submit! {
    ConfigPlugin::new::<OpenAIConfig>("llm:openai", create_openai)
}
```

**embeddings/tei.rs, rerankers/tei.rs (🆕):** HuggingFace Text Embeddings Inference HTTP service — net-new Rust work.

**embeddings/huggingface.rs, rerankers/huggingface.rs (🔧):** Currently stubs that redirect to ONNX. Decide:
- **A)** Keep as stubs (simplest).
- **B)** Implement via `candle` or `tch` crates (adds major Rust deps).

Recommend **A** for v0.6 — Python HF backends exist for authoring; Rust prod uses ONNX exports.

**onnx/backend.rs (🆕):** Extract shared ONNX session/loading code out of `embeddings/onnx.rs` + `rerankers/onnx.rs` into `providers/onnx/backend.rs` matching Python layout.

**Drop list for `providers/` (❌):**
- `ops/chain.py` — `chat()` and `ask()` are **`@graph` builders**, not factories returning Ops. They construct implicit graph structures via `START >> prompt >> llm >> END` at authoring time. Python-authoring-only; Rust consumes the resulting serialized graph, not the builder.

---

### 5b. Logic details resolved after source verification

Points where the plan would silently diverge from Python semantics. Each confirmed against Python source.

**5b.1 `BaseEmbedder` and `BaseReranker` methods are `run()`, not `embed()` / `rerank()`.**
From [embeddings/base.py:11](../../Operon/operonx/providers/embeddings/base.py#L11) and [rerankers/base.py:9](../../Operon/operonx/providers/rerankers/base.py#L9) — both abstract base classes expose a single async method named `run()`. Earlier drafts assumed `embed()`/`aembed()`/`rerank()`/`arerank()` — **wrong.**

Correction — Rust traits must match:
```rust
#[async_trait]
pub trait BaseEmbedder: Send + Sync {
    async fn run(&self, texts: Vec<String>, opts: EmbedOpts) -> Result<EmbedResult, OperonError>;
    fn output_dim(&self) -> usize;
}

#[async_trait]
pub trait BaseReranker: Send + Sync {
    async fn run(&self, query: String, texts: Vec<String>, top_k: usize, threshold: f32) -> Result<Vec<RerankResult>, OperonError>;
}
```
`OnnxInferenceBackend` also uses `run()` per [onnx/backend.py:45](../../Operon/operonx/providers/onnx/backend.py#L45). Same signature shape. §3a.3 method-conventions row for these types must read `run()`, not `embed()`/`rerank()`.

**5b.2 `BaseLLM` exposes `generate()` / `stream()` / `warmup()` / `generate_batch()` — no `agenerate`/`astream`.**
From [llms/base.py:117-280](../../Operon/operonx/providers/llms/base.py#L117). Rust trait:
```rust
#[async_trait]
pub trait BaseLLM: Send + Sync {
    async fn generate(&self, messages: Vec<Message>, opts: &LlmOpts) -> Result<ChatCompletion, OperonError>;
    async fn stream(&self, messages: Vec<Message>, opts: &LlmOpts) -> Result<BoxStream<'_, ChatCompletionChunk>, OperonError>;
    async fn warmup(&self, system_prompt: String) -> Result<(), OperonError>;
    async fn generate_batch(&self, reqs: Vec<BatchReq>, poll_interval: Duration, timeout: Duration, opts: &LlmOpts) -> Result<Vec<ChatCompletion>, OperonError>;
    // base-impl helpers (image encoding) live on a shared helper struct, not the trait
}
```
**No `create()` classmethod on the trait** — factory is standalone `create_llm(config)` at `llms/factory.rs`.

**5b.3 `LLMGenerator` is a static utility class, not a streaming wrapper.**
From [llms/response.py:11-124](../../Operon/operonx/providers/llms/response.py#L11). Methods:
- `parse(line) -> Option<ChatCompletionChunk>` — parse one SSE line
- `make_chunk(content, model, chat_id, last) -> ChatCompletionChunk` — build a synthetic chunk
- `process(stream, model, delay) -> AsyncIterator<Chunk>` — post-process a raw stream (throttle, normalize)
- `simulate(...)` — test helper

Earlier plan draft classified this as 🆕 "wrap for parity." Correction — it's a direct port of the static utility functions. No iterator wrapper to build. Rust:
```rust
pub struct LLMGenerator;
impl LLMGenerator {
    pub fn parse(line: &str) -> Option<ChatCompletionChunk> { ... }
    pub fn make_chunk(content: &str, model: &str, chat_id: &str, last: bool) -> ChatCompletionChunk { ... }
    pub fn process(stream: BoxStream<'_, Value>, model: &str, delay: Duration) -> BoxStream<'_, ChatCompletionChunk> { ... }
}
```
Classification in §5 table: **🔧 reuse concept, rewrite signature — not 🆕.**

**5b.4 `LLMOp` has TWO separate code paths — `_generate_core` and `_stream_core` — not a single unified path.**
From [ops/llm.py:169-172](../../Operon/operonx/providers/ops/llm.py#L169):
```python
if self.stream:
    self._set_core(self._stream_core)
else:
    self._set_core(self._generate_core)
```
Both paths call `_select_llm()` and `_fallback_*` as needed, but the branching is at constructor time.

Earlier migration note referenced "single `_generate_core` + `_stream_core` path" — clarify: **there are two methods and the constructor picks one.** Rust matches this:
```rust
impl LLMOp {
    fn generate_core(&self, inputs: &Value) -> Result<Value, OperonError> { ... }
    fn stream_core(&self, inputs: &Value) -> Result<BoxStream<...>, OperonError> { ... }
    fn core(&self) -> CoreKind {
        if self.meta.stream { CoreKind::Stream(self.stream_core(...)) }
        else                { CoreKind::Generate(self.generate_core(...)) }
    }
}
```

**5b.5 Multi-model selection uses seeded `random.Random` — deterministic only with `seed=`.**
From [ops/llm.py:111](../../Operon/operonx/providers/ops/llm.py#L111):
```python
self._rng = random.Random(seed)       # seed=None → non-deterministic but one-shot per request
# ...
self._rng.choices(self._llms, weights=self.ratios, k=1)[0]
```
Rust equivalent uses `rand::rngs::StdRng::seed_from_u64(seed)` when seed is provided, `StdRng::from_entropy()` when not. One selection per op dispatch — not per-token during streaming.

**5b.6 `create_auth()` only supports Keycloak — not a multi-type dispatcher.**
From [auth/factory.py:7-16](../../Operon/operonx/providers/auth/factory.py#L7). Rust `create_auth()` is simple:
```rust
pub fn create_auth(config: &KeycloakTokenConfig) -> Result<Arc<KeycloakTokenProvider>, OperonError>;
```
Don't over-engineer a plugin dispatcher here; expand only if a second auth provider is added.

**5b.7 `KeycloakTokenProvider` uses BOTH background refresh thread AND lazy fallback.**
From [auth/keycloak.py:133, 195-226](../../Operon/operonx/providers/auth/keycloak.py#L133):
- Background daemon thread wakes at `refresh_interval` to proactively refresh before expiry.
- `get_token()` does lazy fetch if no token cached yet (on first call or after shutdown/restart).
- Both mechanisms coexist; neither is "the" refresh path.

Rust port: `tokio::task::spawn` for background refresh (abort-on-drop) + `OnceLock<RwLock<Token>>` for the cached token. `get_token()` awaits the lock, returns cached if fresh, otherwise triggers a single re-fetch (deduplicated by the lock).

**5b.8 `ops/_utils.py` has only `resolve_hub()` — no `resolve_llm` or `resolve_embedding`.**
From [ops/_utils.py:1-12](../../Operon/operonx/providers/ops/_utils.py#L1). Earlier draft listed extra helpers — wrong. Rust `ops/utils.rs` contains:
```rust
pub fn resolve_hub() -> Arc<ResourceHub> {
    ResourceHub::instance()
}
```
That's it. Don't add helpers that don't exist Python-side.

**5b.9 `BatchCoordinator` is per-LLMOp, not a standalone top-level service.**
From [llms/batch_coordinator.py:110](../../Operon/operonx/providers/llms/batch_coordinator.py#L110) `get_coordinator(resource, llm, ...)` is a per-resource singleton created inside `LLMOp._ensure_initialized()` (line 211). Rust: store `Arc<BatchCoordinator>` inside each `LLMOp` that has `batch_mode=true`. Not a global service.

**5b.10 Plugin auto-registration happens at module import in Python.**
From [providers/__init__.py:14](../../Operon/operonx/providers/__init__.py#L14) importing `operonx.providers.registry` fires `register()` in each plugin file. In Rust, module import doesn't exist at runtime — use `inventory::submit!` at crate-level so registrations are compiled in, then `inventory::collect()` at first `ResourceHub::instance()` construction assembles them. This is already the plan in §5 — just noting that Python's "import triggers register" semantics don't translate literally.

**5b.11 Streaming fallback has a type-discipline gap in Python.**
From [ops/llm.py:305-321](../../Operon/operonx/providers/ops/llm.py#L305), the fallback stream iteration mixes chunk dicts with a final metadata dict without a discriminator. **Don't port the bug.** Rust should use an enum:
```rust
pub enum StreamItem {
    Chunk(ChatCompletionChunk),
    Final { usage: Usage, model_used: String, extras: HashMap<String, Value> },
}
```
Single type, unambiguous handling. Note this as a Python-side issue to fix later.

**5b.12 `providers/__init__.py` exports 40+ symbols.**
Not a gap, but flag — the `mod.rs` `pub use` list is long. Organize by subsection (ops / llms / embeddings / rerankers / auth / onnx / configs / factories) with comment headers.

---

### 5c. Corrected rename for embedder/reranker in §3a.4

Update the §3a.4 table rows:

| Hush-ai Rust (current) | Operon Rust (target) | Python equivalent | Method |
|---|---|---|---|
| `trait EmbeddingProvider` + `fn embed()` | `trait BaseEmbedder` + `async fn run()` | `class BaseEmbedder` + `async def run()` | Method is `run`, not `embed` |
| `trait RerankingProvider` + `fn rerank()` | `trait BaseReranker` + `async fn run()` | `class BaseReranker` + `async def run()` | Method is `run`, not `rerank` |

---

## 6. `operonx/src/telemetry/` — file-by-file mapping

```
operonx/telemetry/ (Python)                    →  operonx/src/telemetry/ (Rust)              Class
──────────────────────────────────────────────────────────────────────────────────────────────────
__init__.py                                   →  mod.rs                                      🔧
plugin.py (auto-register tracer configs)      →  plugin.rs                                   🆕

tracers/__init__.py                           →  tracers/mod.rs                              🔧
tracers/_base.py (ConfigurableTracer)         →  tracers/base.rs                             🆕
tracers/operon_eyes.py                        →  tracers/operon_eyes.rs                      🔧
tracers/langfuse.py                           →  tracers/langfuse.rs                         🔧
tracers/otel.py                               →  tracers/otel.rs                             ⏸ deferred v0.7

backends/__init__.py                          →  backends/mod.rs                             🔧
backends/langfuse/__init__.py                 →  backends/langfuse/mod.rs                    ✅
backends/langfuse/config.py                   →  backends/langfuse/config.rs                 ✅
backends/langfuse/client.py                   →  backends/langfuse/client.rs                 ✅
backends/langfuse/prompt_manager.py           →  backends/langfuse/prompt_manager.rs         🆕
backends/otel/__init__.py                     →  backends/otel/mod.rs                        ⏸ deferred v0.7
backends/otel/config.py                       →  backends/otel/config.rs                     ⏸ deferred v0.7
backends/otel/client.py                       →  backends/otel/client.rs                     ⏸ deferred v0.7
```

### 6a. Detailed notes for `telemetry/`

**Structural move:** Current Hush-ai has flat `hush-telemetry/src/{hush_eyes.rs, langfuse/, otel/}`. Python has the two-level split `tracers/` + `backends/`. Reorganize:

```
Current (hush-telemetry/src/):        →   Target (operonx/src/telemetry/):
├── hush_eyes.rs                          ├── tracers/
├── langfuse/                             │   ├── operon_eyes.rs       ← renamed from hush_eyes.rs
│   ├── mod.rs  (LangfuseTracer)          │   ├── langfuse.rs          ← tracer logic moved out
│   ├── config.rs                         │   └── otel.rs              ← tracer logic moved out
│   └── client.rs                         └── backends/
└── otel/                                     ├── langfuse/
    ├── mod.rs  (OtelTracer)                  │   ├── config.rs        ← unchanged
    ├── config.rs                             │   ├── client.rs        ← unchanged
    └── client.rs                             │   └── prompt_manager.rs 🆕
                                              └── otel/
                                                  ├── config.rs        ← unchanged
                                                  └── client.rs        ← unchanged
```

**tracers/base.rs (🆕):** Python `ConfigurableTracer` abstracts "either direct config OR resource name from hub". Rust tracers currently construct from config directly. Add `ConfigurableTracer` trait/base struct so each tracer's `new()` accepts either `Config` or `resource: String`.

**tracers/operon_eyes.rs (🔧):** Port `hush_eyes.rs` with the rename. Endpoint default: `127.0.0.1:8420/api/ingest`. Verify `to_config_dict()` output matches Python: `{"host": "127.0.0.1", "port": 8420}`.

**tracers/langfuse.rs (🔧):** The tracer-side logic (collect, batch, dispatch to client) stays. Only relocation: split `hush-telemetry/src/langfuse/mod.rs` into `tracers/langfuse.rs` (tracer) + `backends/langfuse/{mod, config, client}.rs`.

**tracers/otel.rs & backends/otel/* (⏸ deferred to v0.7):** OTEL is **not ported to Rust in v0.6**. Rationale:
- The `opentelemetry` Rust crate ecosystem is heavyweight (15+ transitive deps) and prone to tokio-version conflicts.
- Python ships full OTEL support unchanged; users needing OTEL in prod run the Python backend.
- `OTELTracer` doesn't override `to_config_dict()` in Python anyway (§6b.2), so no serialization contract to match.
- Langfuse + OperonEyes cover the common cloud + local observability cases.

Revisit in v0.7 based on demand. Hush-ai's existing OTEL Rust code stays in `Hush-ai/rust/hush-telemetry/otel/` as reference material.

**backends/langfuse/prompt_manager.rs (🆕):** Python ships `LangfusePromptManager` for versioned prompt retrieval + caching from Langfuse cloud. No Rust equivalent. Net-new work.

**plugin.rs (🆕):** Auto-register tracer configs into `ResourceHub` on import. Rust equivalent: `inventory::submit!` calls in `plugin.rs` that execute at startup via `ctor` or first-use lazy init.

---

### 6b. Logic details resolved after source verification

Points where the plan would silently diverge from Python semantics. Each confirmed against Python source.

**6b.1 `to_config_dict()` returns `None` when tracer is built with a `resource=` key — only direct-config tracers are Rust-serializable.**
From [langfuse.py:69-80](../../Operon/operonx/telemetry/tracers/langfuse.py#L69):
```python
def to_config_dict(self):
    if self._config is None:       # resource-based: no config to serialize
        return None
    return {"public_key": ..., "secret_key": ..., "host": ...}
```
Implication for Rust migration:
- `Operon.export_config(path)` (Python-side) must raise a clear error if any tracer was constructed with `resource=` rather than `config=` — Rust can't rehydrate a `ResourceHub` that doesn't exist on the Rust side yet.
- Or: Python export resolves `resource=` to the underlying config dict by calling `ResourceHub.instance().get_config(resource)` and inlining it before serialization. **Recommended** — makes exports self-contained.
- Flag in §4b.1 (`export_config` design): add resource-to-config inlining logic.

**6b.2 `OTELTracer` — DECIDED: Python-only, Rust deferred to v0.7.**
From [otel.py](../../Operon/operonx/telemetry/tracers/otel.py) — `OTELTracer` does not override `to_config_dict()` (inherits `None`). Python OTEL support stays unchanged; Rust does **not** port OTEL in v0.6. See §6a "tracers/otel.rs (⏸ deferred)" for rationale. Python users needing OTEL in prod use the Python backend.

**6b.3 Resource lookup is lazy — deferred to first `flush()`, not at `__init__`.**
From [\_base.py:40-46](../../Operon/operonx/telemetry/tracers/_base.py#L40) + [langfuse.py:171](../../Operon/operonx/telemetry/tracers/langfuse.py#L171). Construction just stores the resource string; `_get_client()` is called from inside `flush()`.

Rust must match:
```rust
pub struct LangfuseTracer {
    config: Option<LangfuseConfig>,
    resource: Option<String>,
    client: OnceLock<Arc<LangfuseClient>>,   // lazy
    tags: Vec<String>,
    trace_filter: Option<TraceFilter>,
}

impl LangfuseTracer {
    fn get_client(&self) -> Result<&Arc<LangfuseClient>, OperonError> {
        self.client.get_or_try_init(|| {
            if let Some(ref c) = self.config { Ok(Arc::new(LangfuseClient::new(c.clone()))) }
            else { ResourceHub::instance().get::<LangfuseClient>(self.resource.as_ref().unwrap()) }
        })
    }
}
```
Constructor does **no** resource validation — mirrors Python's fail-late semantics (§6b.9 below).

**6b.4 `TraceFilter` fields list (exact).**
From [trace_filter.py:49-56](../../Operon/operonx/core/tracing/trace_filter.py#L49):
```rust
pub struct TraceFilter {
    pub skip_empty: bool,                     // default false
    pub exclude_ops: Vec<String>,
    pub include_ops: Vec<String>,             // whitelist mode if non-empty
    pub exclude_kinds: Vec<String>,           // batch / generator / stream_item / ...
    pub protected_types: Vec<String>,         // default: ["trace", "generation"]
    pub max_io_size: usize,                   // default 0 (unlimited)
    pub preserve_children_of: Vec<String>,
    // rewriters: Vec<Callable> — YAML-excluded; Python-only
}
```
`TraceFilter::from_dict(Value)` parses YAML dict, **ignoring unknown keys** and **excluding `rewriters`** (Python-specific custom transforms).

**6b.5 `TraceFilter` op-matching is exact string — not regex, not globs.**
From [trace_filter.py:181-188](../../Operon/operonx/core/tracing/trace_filter.py#L181) — matches `node.display_name == entry` OR `node.op_name == entry` OR `node.op_name.endswith("." + entry)`. Short-name matches the last dotted component. No regex, no wildcards.

Rust `TraceFilter::op_matches(node: &TraceNode, entry: &str) -> bool` implements exactly this. Don't add regex unless Python does first.

**6b.6 `TraceFilter` is loaded once at tracer construction and cached.**
Not hot-reloaded. If a YAML `resources.yaml` is edited at runtime, existing tracer instances keep their old filter. Document this — the Rust behavior matches.

**6b.7 `LangfuseConfig` / `OTELConfig` `_category` discriminator.**
From [langfuse/config.py:37](../../Operon/operonx/telemetry/backends/langfuse/config.py#L37) and [otel/config.py:62](../../Operon/operonx/telemetry/backends/otel/config.py#L62):
- `LangfuseConfig._category = "langfuse"` → ResourceHub keys are `langfuse:<name>` (default: `langfuse:default`).
- `OTELConfig._category = "otel"` → ResourceHub keys are `otel:<name>`.

Rust `ResourceHub` key convention confirmed: `"<category>:<name>"`. Rust config structs carry the category as a const:
```rust
impl LangfuseConfig { pub const CATEGORY: &'static str = "langfuse"; }
impl OTELConfig     { pub const CATEGORY: &'static str = "otel"; }
```

**6b.8 `LangfuseClient` uses stdlib only — no Langfuse SDK. `LangfusePromptManager` DOES require SDK.**
From [client.py:7-12](../../Operon/operonx/telemetry/backends/langfuse/client.py#L7) — only `urllib.request, json, base64, hashlib`. Core tracing path has zero SDK dependency.

From [prompt_manager.py:55-69](../../Operon/operonx/telemetry/backends/langfuse/prompt_manager.py#L55) — lazily imports the `langfuse` SDK only when a prompt is requested.

Rust implications:
- `LangfuseClient` → pure `reqwest::blocking` + `base64`. No `langfuse` crate needed.
- `LangfusePromptManager` → needs the `langfuse` Rust SDK (does it exist on crates.io?) **Verify crate availability during Phase 7; if absent, port this as a direct HTTP client against Langfuse's prompt API rather than depending on an SDK.** Add a TODO in the plan.

**6b.9 Resource name is not validated at `__init__` — fail-late on first `flush()`.**
From [\_base.py:40-50](../../Operon/operonx/telemetry/tracers/_base.py#L40). A typo like `resource="langfse:default"` builds a tracer successfully; error surfaces only when the first trace flushes. This is intentional (allows tracer construction before hub is populated).

Rust matches. Document as expected behavior, not a bug to fix.

**6b.10 `flush(trace_data)` input is a fully-serialized dict, NOT `TraceNode` objects.**
From [langfuse.py:160-168](../../Operon/operonx/telemetry/tracers/langfuse.py#L160) docstring + [langfuse.py:173-198](../../Operon/operonx/telemetry/tracers/langfuse.py#L173) usage:
```json
{
  "request_id": "...",
  "workflow_name": "...",
  "user_id": "...",
  "session_id": "...",
  "tags": ["..."],
  "nodes": [ {"trace_key": ..., "kind": ..., ...}, ... ]  // list of dicts
}
```
Nodes are already dicts at this point — serialization happens in `TraceCollector` before handing to tracer.

Rust `trait Tracer::flush(&self, trace_data: &TraceData)` where:
```rust
pub struct TraceData {
    pub request_id: String,
    pub workflow_name: String,
    pub user_id: Option<String>,
    pub session_id: Option<String>,
    pub tags: Vec<String>,
    pub nodes: Vec<serde_json::Value>,   // pre-serialized
}
```

**6b.11 `plugin.py` uses module-level `_registered` guard.**
From [plugin.py:32-34](../../Operon/operonx/telemetry/plugin.py#L32). Idempotent on repeated import.

Rust `inventory::submit!` is naturally idempotent (compile-time), so this gap disappears. No special handling needed.

**6b.12 `LangfuseTracer.flush()` handles media uploads inline — sync, soft-fail.**
From [langfuse.py:240, 144-155](../../Operon/operonx/telemetry/tracers/langfuse.py#L240) — `_upload_node_media()` does sync HTTP (metadata POST + binary PUT), failures logged but not raised. No retry.

Rust implementation must match: soft-fail via `log::warn!`, continue without re-throwing. Not a design gap, but document behavior.

**6b.13 `OperonEyesTracer` hardcodes `stream_trace_limit=None`.**
From [operon_eyes.py:38](../../Operon/operonx/telemetry/tracers/operon_eyes.py#L38) — bypasses the base class default of 100. Rationale: ui-operonx-eyes handles large traces locally without size caps.

Rust `OperonEyesTracer::new()` also hardcodes `None` on the base. Don't expose `stream_trace_limit` on its constructor.

---

## 7. `operonx-macros/` — proc-macros

Port 1:1 from `rust/hush-macros/`, but export with **short names** that match Python's `@op` symmetry:

- `#[op]` (was `#[hush_op]`) — registers op factories via `inventory`; supports `generator`, `name = "..."`, typed-signature auto-serde wrapper.
- `#[resource]` (was `#[hush_resource]`) — registers resource factories.
- `#[model]` (was `#[hush_model]`) — derives Serialize/Deserialize/Debug/Clone.

**Usage:**
```rust
use operonx::{op, resource, model};

#[op(name = "my_ops.double")]
fn double(x: i64) -> serde_json::Value {
    serde_json::json!({"result": x * 2})
}
```

**Tradeoff acknowledged:** `op`, `resource`, `model` are common identifiers. Users who have their own `struct Op`, `fn resource()`, etc. will hit name clashes and must either rename their own or alias the macro (`use operonx::op as operon_op;`). This is accepted for the ergonomic win of mirroring Python's decorator.

Classification: **✅ Reuse as-is** (renames + name shortening).

---

## 8. Test strategy — `internal/` + `spec/` split, shared JSON fixtures

### 8.1 Two zones, symmetric on both sides

| Zone | Purpose | Mirrored? |
|---|---|---|
| `internal/` | Backend-specific tests — Python's `@op` decorator, `@graph` builder, `>>` operator, `ExecutionHandle` async-iter, `GraphOp` context-mgr; Rust's tokio dispatch, `inventory::submit!` registration, `OnceLock`, thread safety. Exercises implementation details one language can't see in the other. | **No.** Each side has its own. |
| `spec/` | Behavioral contract — serialized graph + inputs → expected outputs. Backend-agnostic. | **Yes.** Path-for-path, both sides read the *same* fixture files. |

### 8.2 Layout

```
Operon/                                       ← Python repo root
├── operonx/                                    ← package source
├── tests/
│   ├── internal/                              ← Python-internal tests
│   │   ├── core/
│   │   │   ├── test_graph_builder.py          ← @graph, >> operator
│   │   │   ├── test_auto_name.py
│   │   │   ├── test_execution_handle.py       ← async-iter, cancel mid-flight
│   │   │   └── ...
│   │   ├── providers/
│   │   │   ├── test_op_decorator.py           ← @op internals
│   │   │   └── ...
│   │   └── telemetry/
│   │       └── ...
│   └── spec/                                  ← shared fixtures
│       ├── test_fixtures.py                   ← ONE driver file — parametrizes over fixture folders
│       ├── core/
│       │   ├── scheduler/
│       │   │   ├── streaming_basic/
│       │   │   │   ├── graph.json
│       │   │   │   ├── inputs.json
│       │   │   │   └── expected.json
│       │   │   ├── parallel_policy/
│       │   │   │   ├── graph.json
│       │   │   │   ├── inputs.json
│       │   │   │   └── expected.json
│       │   │   └── ...
│       │   ├── ops/
│       │   │   └── branch_routing/
│       │   └── state/
│       │       └── context_hierarchy/
│       ├── providers/
│       │   └── llm_fallback_chain/
│       └── telemetry/
│           └── langfuse_batch_shape/
│               ├── graph.json
│               ├── inputs.json
│               └── expected_trace.json        ← trace output shape
└── rust/
    └── operonx/
        ├── src/                                ← units tests inline via #[cfg(test)]
        │   └── **/*.rs
        └── tests/
            ├── common/
            │   └── mod.rs                      ← load_fixture(), assert_eq_ignoring_timing()
            ├── internal/                        ← Rust-internal integration tests
            │   ├── core/
            │   │   ├── mod.rs                   ← pub mod scheduler; pub mod ref_transforms; ...
            │   │   ├── scheduler.rs
            │   │   ├── ref_transforms.rs
            │   │   └── state.rs
            │   ├── providers/
            │   │   ├── mod.rs
            │   │   ├── http_pool.rs
            │   │   └── inventory_registration.rs
            │   └── telemetry/
            │       ├── mod.rs
            │       └── thread_safety.rs
            ├── spec/                             ← mirrors Python tests/spec/ exactly
            │   ├── core/
            │   │   ├── mod.rs
            │   │   ├── scheduler/
            │   │   │   ├── mod.rs
            │   │   │   ├── streaming_basic.rs
            │   │   │   └── parallel_policy.rs
            │   │   ├── ops/
            │   │   │   ├── mod.rs
            │   │   │   └── branch_routing.rs
            │   │   └── state/
            │   │       ├── mod.rs
            │   │       └── context_hierarchy.rs
            │   ├── providers/
            │   │   ├── mod.rs
            │   │   └── llm_fallback_chain.rs
            │   └── telemetry/
            │       ├── mod.rs
            │       └── langfuse_batch_shape.rs
            ├── internal_core.rs                  ← Rust mechanics — one binary per area
            ├── internal_providers.rs
            ├── internal_telemetry.rs
            ├── spec_core.rs
            ├── spec_providers.rs
            └── spec_telemetry.rs
```

### 8.3 Fixture contract

Each shared test is a **folder** containing JSON files:

```
tests/spec/<area>/<category>/<test_name>/
├── graph.json          ← output of Operon.export_config() — the serialized workflow
├── inputs.json         ← inputs dict passed to engine.run()
└── expected.json       ← expected outputs dict (or trace data for telemetry tests)
```

Optional additional files for advanced cases:
- `seed.json` — RNG seed (for load-balanced LLM tests).
- `env.json` — env var stubs (for resource-hub tests).
- `resources.yaml` — local resource hub override.
- `expected_trace.json` — expected trace node tree (telemetry parity).
- `mock_llm.json` — pre-recorded LLM responses (deterministic provider tests).

### 8.4 Harness skeletons

**Python** — one driver file `tests/spec/test_fixtures.py` parametrized over every fixture folder:

```python
# tests/spec/test_fixtures.py
import json, pytest
from pathlib import Path
from operonx import Operon

SPEC_ROOT = Path(__file__).parent

def iter_fixtures():
    for fx in SPEC_ROOT.rglob("graph.json"):
        yield fx.parent

@pytest.mark.parametrize(
    "fx", list(iter_fixtures()),
    ids=lambda p: str(p.relative_to(SPEC_ROOT))
)
def test_fixture(fx):
    graph    = json.loads((fx / "graph.json").read_text())
    inputs   = json.loads((fx / "inputs.json").read_text())
    expected = json.loads((fx / "expected.json").read_text())
    result = Operon(graph).run(inputs)
    assert _equal_ignoring_timing(result, expected)
```

Result: each fixture folder becomes one pytest ID:
```
tests/spec/test_fixtures.py::test_fixture[core/scheduler/streaming_basic]
tests/spec/test_fixtures.py::test_fixture[providers/llm_fallback_chain]
```

**Rust** — one binary per area, each pulls in the `spec/` module tree:

```rust
// operonx/tests/spec_core.rs
mod common;
#[path = "spec/core/mod.rs"] mod spec_core;
```

```rust
// operonx/tests/spec/core/scheduler/streaming_basic.rs
use crate::common::run_fixture;

#[test]
fn streaming_basic() {
    run_fixture("core/scheduler/streaming_basic");
}
```

```rust
// operonx/tests/common/mod.rs
use operonx::Operon;
use serde_json::Value;

pub fn load(path: &str) -> Value {
    let full = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/spec")       // points at Python repo's fixtures
        .join(path);
    serde_json::from_str(&std::fs::read_to_string(full).unwrap()).unwrap()
}

pub fn run_fixture(test_name: &str) {
    let graph    = load(&format!("{}/graph.json",    test_name));
    let inputs   = load(&format!("{}/inputs.json",   test_name));
    let expected = load(&format!("{}/expected.json", test_name));
    let engine = Operon::new(&graph.to_string()).unwrap();
    let result = engine.run_json(inputs, None, None, None).unwrap();
    assert_eq_ignoring_timing(&result, &expected);
}
```

### 8.5 Canonical fixtures — single source of truth

The Python repo owns `Operon/tests/spec/`. Rust reads the same files:

- **Dev:** relative path `../../tests/spec/` from the Rust crate (works because both live in `Operon/`).
- **Publish:** include the fixture folder in the `operonx` crate via `Cargo.toml`:
  ```toml
  [package]
  include = ["src/**", "tests/spec/**", "../../tests/spec/**"]
  ```

Fixtures are small (JSON); distributing them keeps `cargo test -p operonx` self-contained after publish.

### 8.6 Handling timing and non-determinism

- `$start_time`, `$end_time`, `$duration_ms` vary per run — `_equal_ignoring_timing` / `assert_eq_ignoring_timing` strip these keys before comparison.
- LLM/embedding tests use deterministic mocks via `mock_llm.json` — no live API calls in CI.
- Load-balanced multi-model tests declare `seed.json`; both sides seed their RNG from it and must produce identical selection sequences.

### 8.7 No `__init__.py` in `tests/internal/` or `tests/spec/`

Keep both as plain directories, not Python packages. This lets pytest auto-discover `test_*.py` files transparently without import-path headaches, and keeps fixture subdirectories from being misinterpreted as packages.

### 8.8 Running tests

**Python:**
```bash
pytest                              # all tests (internal + spec)
pytest tests/internal               # Python-internal only
pytest tests/spec                   # shared fixtures only
pytest tests/spec -k streaming      # filter by fixture name
pytest "tests/spec/test_fixtures.py::test_fixture[core/scheduler/streaming_basic]"
```

**Rust:**
```bash
cargo test -p operonx                          # all Rust tests (inline + internal + spec)
cargo test -p operonx --test spec_core         # shared spec for core only
cargo test -p operonx --test internal_core     # Rust-internal for core only
cargo test -p operonx --test spec_core streaming_basic
```

### 8.9 What belongs in each zone — decision rule

**Shared (`spec/`)** if and only if:
1. Expressible as `(graph, inputs) → expected_outputs` (or `→ expected_trace` for telemetry).
2. No Python-specific or Rust-specific API in the test body.
3. Non-determinism is controllable (seeds, mocks, env stubs).

**Internal** otherwise. Typical internal-only cases:

Python-internal:
- `@op` / `@graph` decorator behavior.
- `GraphOp` context-manager build logic.
- `ExecutionHandle.__aiter__`, `await handle[op, var]`, `cancel()` mid-flight.
- Ref DSL ergonomics (`op["x"] + 5`).
- `Operon.export_config()` output-shape tests.

Rust-internal:
- `tokio::spawn` vs `rayon::spawn` dispatch selection.
- `inventory::submit!` registration collection.
- `OnceLock` / `Mutex` thread safety.
- `SmallVec` context-id spill-to-heap behavior.
- `reqwest::blocking` connection pool reuse.
- `serde::Deserialize` rejecting unknown config fields.

### 8.10 Migrating existing Python tests

Current `Operon/tests/{core, providers, telemetry}/*.py` get audited during Phase 9 of §11:
1. `git mv tests/{core,providers,telemetry} tests/internal/` (one-time relocation).
2. For each test function in `tests/internal/**/test_*.py`: if purely behavioral (`build graph, run, assert outputs`), extract `(graph, inputs, expected)` as a fixture under `tests/spec/...`, delete the Python version. Otherwise leave it in `internal/`.
3. Cross-file: one `test_scheduler.py` may donate 8 fixtures to `spec/` and keep 3 tests in `internal/`.

Mechanical ~1-2 days of work. Not upfront — do it alongside Rust parity verification.

### 8.11 CI enforcement

- **Python CI:** `pytest tests/` runs both zones.
- **Rust CI:** `cargo test -p operonx` runs all three tiers (inline unit + internal + spec).
- **Parity check job:**
  - Enumerate fixture folders: `find Operon/tests/spec -name graph.json -printf '%h\n' | sort`
  - Enumerate Rust spec tests: `grep -rh '^fn ' operonx/tests/spec/**/*.rs` → parsed names.
  - Diff — fail CI if a fixture folder has no Rust twin or vice versa. Catches drift at PR time.

---

## 9. Summary of reusable vs new work

### Reusable from Hush-ai Rust (✅ or 🔧)

| Area | Count | Notes |
|---|---|---|
| Ref transforms | 1 file | `refs/ref_transforms.rs` — full parity, just relocate to `core/states/ref.rs` |
| EngineState | 1 file | `states/state.rs` — keep DashMap+lasso, wrap as MemoryState |
| BaseOp / Op trait | 2 files | merge `op_trait.rs` + `base.rs` into `core/ops/base.rs` |
| FuncOp / ParserOp / BranchOp | 3 files | as-is (relocate) |
| GraphOp + scheduler | 2 files | split scheduler out; add stream policies |
| Cache | 1 file | as-is |
| Runtime | 1 file | as-is |
| LLM providers | 5 files | OpenAI, Azure, Gemini, Anthropic + shared types |
| Embedding providers | 2 files | OpenAI, ONNX (vLLM shares openai.rs) |
| Reranker providers | 4 files | vLLM, Cohere, Pinecone, ONNX |
| Auth | 2 files | Keycloak, Google |
| OpenAI Batch coordinator | 1 file | relocate to `llms/` |
| Telemetry backends | 4 files | Langfuse config/client, OTEL config/client |
| Macros | 3 macros | rename only |
| **Total reusable** | **~35 files** | **roughly 80% of Rust LOC** |

### New work (🆕)

| Area | Files | Effort |
|---|---|---|
| `core/media.rs` | 1 | Small |
| `core/ops/edges.rs`, `params.rs` | 2 | Small |
| `core/ops/graph/validation.rs` | 1 | Medium (optional) |
| `core/states/schema.rs` | 1 | **Medium-large** (StateSchema with compiled refs + stream policies) |
| `core/states/cell.rs` | 1 | Small |
| `core/registry/resource_hub.rs` | 1 | **Large** (singleton, YAML, env interpolation, health check) |
| `core/registry/config_registry.rs` | 1 | Small |
| `core/registry/storage/{base,yaml,json}.rs` | 3 | Medium |
| `core/registry/shortcuts/health.rs` | 1 | Small |
| `core/tracing/labels.rs`, `trace_filter.rs` | 2 | Small |
| `core/engine.rs` — `ExecutionHandle` | 1 | **Large** (Frame/EOF stream, `wait_for`, `collect`, `cancel`) |
| `core/middleware.rs` wiring | — | Small (trait exists; wire it up) |
| `core/ops/graph/task_scheduler.rs` — stream policies | — | **Medium** (Parallel/Collect modes + semaphore) |
| `core/exceptions.rs` — split into hierarchy | — | Small |
| `core/configs/` split | — | Small (extraction from config.rs) |
| `core/loggings/` split | — | Small (one file → module tree) |
| `providers/ops/triton.rs` | 1 | **Medium** (gRPC + HTTP) |
| `providers/ops/*` lazy-init refactor | 5 | Small per file |
| `providers/embeddings/tei.rs`, `rerankers/tei.rs` | 2 | Medium |
| `providers/onnx/backend.rs` | 1 | Small (extract) |
| `providers/registry/*_plugin.rs` | 5 | Small each |
| `telemetry/tracers/base.rs` (ConfigurableTracer) | 1 | Small |
| `telemetry/backends/langfuse/prompt_manager.rs` | 1 | Medium |
| `telemetry/plugin.rs` | 1 | Small |
| `telemetry/tracers/*` structural split | 3 | Small (already exist, relocate) |
| **Total new** | **~35 files** | **roughly 20% of Rust LOC** |

### Drop (❌)

All are Python authoring-layer concepts that don't reach the runtime:
- `core/ops/_shortcuts.py`, `core/ops/_utils.py`
- `core/ops/graph/_decorators.py`
- `core/utils/context.py` (`_current_graph` tracking)
- `core/utils/auto_name.py` (op auto-naming during graph build)

Plus already-known drops:
- `hush-plugin` crate (deprecated cdylib path — not part of this migration anyway).

---

## 10. Schema contract lock-in

Since Python and Rust crates will publish independently (`operonx` on PyPI, `operonx` on crates.io), freeze the `graph.serialize()` + `tracer.to_config_dict()` output as versioned contracts:

- Add `"schema_version": "1.0"` at the top level of every graph serialization.
- Rust rejects unknown major versions with `OperonError::UnsupportedSchema`.
- Bump **minor** on additive changes, **major** on breaking.

---

## 11. Phased migration

Each phase ends with `cd rust && cargo test --workspace` passing.

### Phase 0 — Scaffold (0.5 day)

- Create `Operon/rust/` workspace with two crates: `operonx`, `operonx-macros`.
- Mirror empty `core/`, `providers/`, `telemetry/` trees under `operonx/src/` matching Python.
- Mirror empty `tests/internal/` and `tests/spec/` trees under `operonx/tests/`.
- Add the concrete Cargo.toml pins below.
- CI: `cd rust && cargo build --workspace && cargo test --workspace` green on empty tree.

**`Operon/rust/Cargo.toml` (workspace root):**

```toml
[workspace]
members  = ["operonx", "operonx-macros"]
resolver = "2"

[workspace.package]
edition      = "2021"
rust-version = "1.75"
version      = "0.6.0"
license      = "Apache-2.0"
repository   = "https://github.com/<org>/operonx"

[workspace.dependencies]
# Runtime
tokio              = { version = "1.40", features = ["full"] }
tokio-util         = { version = "0.7", features = ["rt"] }
futures            = "0.3"
async-stream       = "0.3"
async-trait        = "0.1"

# Concurrency & interning
dashmap            = "6.1"
lasso              = { version = "0.7", features = ["multi-threaded"] }
parking_lot        = "0.12"
rayon              = "1.10"
smallvec           = { version = "1.13", features = ["serde"] }

# Serialization
serde              = { version = "1.0", features = ["derive"] }
serde_json         = "1.0"
serde_yaml         = "0.9"
thiserror          = "1.0"

# HTTP / env
reqwest            = { version = "0.12", default-features = false, features = ["blocking", "rustls-tls", "json"] }
dotenvy            = "0.15"

# Tracing & logging
tracing            = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter", "json"] }

# Plugin registration
inventory          = "0.3"

# Random
rand               = { version = "0.8", features = ["std_rng"] }

# Time / crypto helpers
chrono             = { version = "0.4", features = ["serde"] }
base64             = "0.22"

# Optional: ONNX
# TODO(phase-0): ort 2.0 hasn't shipped stable yet — check crates.io for the latest
# 2.0.0-rc.N at scaffold time and pin exact (=X.Y.Z-rc.N). Current as of writing: rc.9.
# Consider ort 1.16.x if RC churn becomes painful; requires inference code rewrite.
ort                = { version = "=2.0.0-rc.9", optional = true }
tokenizers         = { version = "0.20", optional = true }

# Optional: Triton
tonic              = { version = "0.12", optional = true }
prost              = { version = "0.13", optional = true }
```

**`Operon/rust/operonx/Cargo.toml`:**

```toml
[package]
name                = "operonx"
version.workspace   = true
edition.workspace   = true
rust-version.workspace = true
license.workspace   = true
description         = "High-performance Rust execution backend for Operon workflows"

[dependencies]
operonx-macros      = { version = "0.6.0", path = "../operonx-macros" }
tokio.workspace     = true
tokio-util.workspace = true
futures.workspace   = true
async-stream.workspace = true
async-trait.workspace = true
dashmap.workspace   = true
lasso.workspace     = true
parking_lot.workspace = true
rayon.workspace     = true
smallvec.workspace  = true
serde.workspace     = true
serde_json.workspace = true
serde_yaml.workspace = true
thiserror.workspace = true
reqwest.workspace   = true
dotenvy.workspace   = true
tracing.workspace   = true
tracing-subscriber.workspace = true
inventory.workspace = true
rand.workspace      = true
chrono.workspace    = true
base64.workspace    = true
ort                 = { workspace = true, optional = true }
tokenizers          = { workspace = true, optional = true }
tonic               = { workspace = true, optional = true }
prost               = { workspace = true, optional = true }

[features]
default       = ["langfuse", "operonx-eyes"]
langfuse      = []
operonx-eyes   = []
onnx          = ["dep:ort", "dep:tokenizers"]
triton        = ["dep:tonic", "dep:prost"]
# otel: intentionally not listed — deferred to v0.7

[package.metadata.docs.rs]
all-features = true

[package]
include = ["src/**", "README.md", "LICENSE", "../../tests/spec/**"]
```

**`Operon/rust/operonx-macros/Cargo.toml`:**

```toml
[package]
name              = "operonx-macros"
version.workspace = true
edition.workspace = true
rust-version.workspace = true
license.workspace = true
description       = "Proc macros for Operon — #[op], #[resource], #[model]"

[lib]
proc-macro = true

[dependencies]
proc-macro2 = "1.0"
quote       = "1.0"
syn         = { version = "2.0", features = ["full"] }
```

**Key choices locked in:**
- MSRV: **Rust 1.75** (conservative; can bump if needed later).
- TLS: **rustls** (no OpenSSL dep, clean cross-platform).
- HTTP: `reqwest::blocking` for tracers (matches Python sync `flush()`), async `reqwest` for provider ops.
- `async-trait` required (stable Rust doesn't yet support async methods in `dyn Trait`).
- `inventory` for compile-time `#[op]` / `#[resource]` registration — no `ctor`, no runtime startup code.
- Workspace dependency table — every sub-crate inherits versions via `.workspace = true`. Single bump point.
- Fixtures bundled into the published `operonx` crate via `include = [".../tests/spec/**"]` so `cargo test -p operonx` works post-publish.

### Phase 1 — Core I: relocate reusables (2 days)
- Port `refs/ref_transforms.rs` → `core/states/ref.rs`.
- Port `states/state.rs` → `core/states/state.rs` (keep EngineState, wrap as MemoryState).
- Port `ops/op_trait.rs` + `ops/base.rs` → `core/ops/base.rs`.
- Port `ops/{cache, flow/branch_op, transform/func_op, transform/parser_op}.rs` to matching paths.
- Port `runtime.rs` → `core/runtime.rs` (module-private util).
- Port `config.rs` (split into `core/configs/{edge_config, op_config}.rs` + `core/ops/params.rs`).
- Translate logging templates, split `logging.rs` → `core/loggings/`.
- Smoke: single-op graph round-trips.

### Phase 2 — Core II: new abstractions (3-4 days)
- **`core/states/schema.rs`** — StateSchema with compiled refs + stream policies.
- **`core/states/cell.rs`** — Cell wrapper.
- **`core/registry/resource_hub.rs`** — singleton + YAML loader + env interpolation + missing-var aggregation.
- **`core/registry/config_registry.rs`** + **`core/registry/storage/*`** + **`core/registry/shortcuts/health.rs`**.
- **`core/middleware.rs`** — wire existing trait into engine.
- **`core/exceptions.rs`** — split RushError into OpError hierarchy.
- **`core/media.rs`**.
- **`core/ops/edges.rs`, `params.rs`**.
- Test: `ResourceHub::from_yaml` + env interpolation.

### Phase 3 — Engine + ExecutionHandle (3 days)
- Rewrite `core/engine.rs`:
  - `Operon::new()` auto-loads `.env` + `resources.yaml`.
  - `OperonBuilder` for explicit hub/tracers/middleware.
  - `run_json`, `run_json_async`, `start()`.
- **`ExecutionHandle`** as `Stream<Item = FrameEvent>` + `wait_for`, `collect`, `cancel`.
- Middleware invocation chain.
- Smoke: Python `@op double(x=5)` serialized → runs in Rust → returns `{"result": 10}`.

### Phase 4 — Scheduler: stream policies + loops (2-3 days)
- Split scheduler out of `graph_op.rs` → `core/ops/graph/task_scheduler.rs`.
- Add `StreamPolicy::{Sequential, Parallel, Collect}` with `max_stream_concurrent` semaphore.
- Verify `LoopConfig` parsing matches Python's serialized `until: RefConfig`, `loop_vars` carry-forward.
- Verify soft-edge semantics (one predecessor unblocks).
- Port the 13 integration tests from `hush-icore/tests/`.

### Phase 5 — Providers: backends (3 days)
- Port LLM providers (OpenAI, Azure, Gemini, Anthropic) → `providers/llms/`.
- Port embedding providers (OpenAI, ONNX, vLLM split) → `providers/embeddings/`.
- Port reranker providers (vLLM, Pinecone, Cohere, ONNX) → `providers/rerankers/`.
- Port auth (Keycloak, Google) → `providers/auth/`.
- Port `onnx/` (extract shared backend).
- Relocate OpenAI Batch coordinator → `providers/llms/batch_coordinator.rs`.

### Phase 6 — Providers: ops + plugins (3 days)
- Port `providers/ops/{llm, embedding, rerank, onnx, prompt, chain}.rs` with **lazy init via ResourceHub**.
- Implement **`providers/ops/triton.rs`** (new).
- Port `providers/ops/factory.rs`.
- Implement **`providers/registry/*_plugin.rs`** (auto-register via `inventory`).
- Implement **`providers/embeddings/tei.rs`, `providers/rerankers/tei.rs`** (new).
- Wire LLM streaming into `ExecutionHandle`.

### Phase 7 — Telemetry (2 days)
- Relocate `hush_eyes.rs` → `telemetry/tracers/operon_eyes.rs`.
- Split `langfuse/` and `otel/` into `telemetry/tracers/*.rs` (tracer) + `telemetry/backends/*/` (client+config).
- Implement **`telemetry/tracers/base.rs`** (ConfigurableTracer).
- Implement **`telemetry/backends/langfuse/prompt_manager.rs`**.
- Implement **`telemetry/plugin.rs`**.
- Extend **`core/tracing/models.rs`**: `kind`, `media`, `usage`, `cost`, `thinking_content`, `parent_trace_key`.
- Rewrite **`core/tracing/collector.rs`** to walk graph+state and emit full TraceNode tree.

### Phase 8 — Macros (0.5 day)
- Port `hush-macros` → `operonx-macros`. Export as `#[op]`, `#[resource]`, `#[model]` (short names). Keep typed-signature auto-serde wrapper.

### Phase 9 — Parity verification (2-3 days)
- Select 10 Python tests from `tests/core/`, `tests/providers/`, `tests/telemetry/`.
- For each: serialize graph + inputs to JSON → run under Rust → assert identical outputs + timing key presence.
- Schema round-trip: `#[serde(deny_unknown_fields)]` on every struct; Python `graph.serialize()` must deserialize cleanly.
- Provider HTTP mocks via `wiremock`.
- Tag `operonx v0.6.0` on crates.io.

**Total estimate: ~3-4 weeks**, with Phases 1 + 5 the clear "bulk porting" phases and Phases 2 + 3 + 6 + 7 the "new work" bottlenecks.

---

## 12. Open questions deferred (within scope)

- HuggingFace-native (non-ONNX) Rust backends — defer to v0.7.
- Per-provider tokenizers (token counting) — defer.
- Distributed tracing span correlation for OTEL — verify in Phase 9.
- Advanced cache invalidation policies (beyond TTL) — defer.

*Out-of-scope items (HTTP server, trace viz, cdylib plugins) are **not tracked here** — separate migrations.*

---

## 13. Deliverables

- `Operon/rust/operonx/` v0.6.0 on crates.io.
- `Operon/rust/operonx-macros/` v0.6.0 on crates.io.
- Python↔Rust schema locked at `"schema_version": "1.0"`.
- All 10 selected parity tests green.
- This document updated with any deviations encountered during implementation (see §14).

---

## 14. Deviations encountered during implementation

Documenting the delta between the plan as written and what actually landed. Future updates to the plan should fold these in.

### 14.1 Stream policy serialization gap (Phase 4)

**Plan call:** port `StreamPolicy::{Sequential, Parallel, Collect}` dispatch.

**As-implemented:**
- Rust scheduler honours `RefConfig.stream_policy` — frames route sequentially (default), all-at-once (`parallel`), or buffer-then-flush (`collect` + `__collect__` sub-context).
- `max_stream_concurrent` semaphore enforces the global cap; per-edge `parallel_max` is read but not yet enforced with a dedicated sub-semaphore (left as a follow-up; uses global cap conservatively).
- **Python `Ref.serialize()` does not yet emit `stream_policy`** — [operonx/core/states/ref.py:594-601](operonx/core/states/ref.py#L594-L601) returns `{source, var, transforms, is_output}` only. Rust-native callers set `stream_policy` directly on graph JSON; Python → Rust round-trip falls back to sequential until Python serialization is extended. Tracked in [rust/operonx/src/core/states/ref.rs](rust/operonx/src/core/states/ref.rs) (field doc comment).

### 14.2 `providers/ops/chain.rs` — builder helpers instead of runtime ops (Phase 6)

**Plan call:** "Port `providers/ops/chain.py` to Rust with lazy init via ResourceHub."

**As-implemented:** Python's `chat()` / `ask()` are `@graph`-decorated authoring-layer helpers — they run at Python build time and never reach the runtime. The Rust port ships `chain::build_chat_graph(ChatArgs)` and `chain::build_ask_graph(AskArgs)` as **graph-shape builders** that emit the same JSON Python's decorator emits. Rust-native callers get the same ergonomics; the serialized JSON still feeds the scheduler unchanged.

### 14.3 Loop iteration emits fewer frames than naive port (Phase 4)

**Plan call:** "Verify `LoopConfig` parsing matches Python's serialized `until: RefConfig`, `loop_vars` carry-forward."

**As-implemented:** Python's `task_scheduler.py:132` runs subsequent loop iterations with `output_queue=None`, meaning **only the first iteration emits per-op frames**; the final state is published once at the end. Rust now mirrors this via `FrameSender::silent()` — subsequent iterations use a clone with a `silent` flag that no-ops on the public channel (trace tap still captures). Without this, Rust emitted one frame per iteration and `ExecutionHandle::collect(Group, true)` returned a longer list than Python's.

### 14.4 `LLMBaseFields` flatten → inlined (Phase 9)

**Plan call:** `#[serde(deny_unknown_fields)]` on every config struct.

**As-implemented:** `serde`'s documented limitation: `deny_unknown_fields` cannot combine with `#[serde(flatten)]`. `LLMBaseFields` (proxy, cost_per_input_token, cost_per_output_token) was previously flattened into every concrete LLM config; those three fields are now inlined directly into `OpenAIConfig`, `AzureConfig`, `GeminiConfig`, `AnthropicConfig`, each decorated with `deny_unknown_fields`. `LLMBaseFields` remains as a convenience constructor for ad-hoc Rust-side use.

### 14.5 Cross-language `func_name` resolution (Phase 8)

**Plan call:** `#[op]` auto-registration via `inventory`.

**As-implemented:** Python emits `"func_name": "tests.spec._ops.increment"` (fully qualified); Rust's `#[op]` typically registers the bare name (`"increment"`). The scheduler's op-dispatch path tries the exact name first, then falls back to the last dotted component. Documented inline at [rust/operonx/src/core/ops/graph/task_scheduler.rs](rust/operonx/src/core/ops/graph/task_scheduler.rs) (in `execute_op`).

### 14.6 Graph-input literals seeded into PARENT (Phase 4)

**Plan call:** no explicit mention.

**As-implemented:** Python-emitted graphs serialize `GraphOp.loop(count=0)` as `graph.inputs.count.literal = 0`. The Rust scheduler previously only seeded PARENT state from the caller-provided `inputs` dict; it now also pulls literal/default values from `graph.inputs` at `run_once` start, with caller inputs taking precedence. Required for loops that seed their own state and any graph with literal defaults.

### 14.7 `compute_out_vars` accepts both `__PARENT__` and graph-name refs (Phase 4)

**Plan call:** no explicit mention.

**As-implemented:** Python serializes `PARENT["count"]` as `{"source": "main", ...}` (the graph's own name) rather than the sentinel `"__PARENT__"`. The scheduler's output-forwarding logic now recognises both forms — `source == "__PARENT__"` OR `source == graph.name` OR `source == graph.full_name`.

### 14.8 Test tree — Python split + shared fixtures at repo root (Phase 9, §8)

**Plan call:** `tests/{core,providers,telemetry}/` → `tests/internal/`; shared fixtures at `tests/spec/`.

**As-implemented:** Done. Shared JSON fixtures live at `Operon/tests/spec/` (single source of truth); Rust reads via `../../tests/spec/` (dev) or the Cargo-bundled in-crate copy (post-publish, via `include = [..., "../../tests/spec/**"]`). Each fixture folder contains `graph.json` + `inputs.json` + `expected.json` (both runtimes read) plus `builder.py` (Python constructs the GraphOp via the authoring DSL).

11 fixtures landed across `core/{scheduler, ops, state, iteration}`. The parity CI job in [.github/workflows/parity.yaml](.github/workflows/parity.yaml) enforces that every fixture folder has both `graph.json` (Rust consumer) **and** `builder.py` (Python consumer).

### 14.9 Duplicate `#[op]` qualified names now panic (Phase 8)

**Plan call:** no explicit mention (Hush parity).

**As-implemented:** `OperonBuilder::auto_register()` panics when two `#[op]` submissions share `(module_path, name)`. Silent overwrite (DashMap default) hid real bugs in link order; explicit panic matches Hush's stricter semantics. Bare-name duplicates are still tolerated but become reachable only via the qualified name.

### 14.10 `wiremock` dev-dep (Phase 9)

**Plan call:** "Provider HTTP mocks via `wiremock`."

**As-implemented:** `wiremock = "0.6"` added to `[dev-dependencies]` in [rust/operonx/Cargo.toml](rust/operonx/Cargo.toml). Two OpenAI LLM smoke tests land in [rust/operonx/tests/internal/providers/wiremock_llm.rs](rust/operonx/tests/internal/providers/wiremock_llm.rs) — one happy path, one 429 error path. Embedding / reranker provider mocks deferred to a follow-up batch.

### 14.11 OTEL deferred to v0.7 (Phase 7)

**Plan call:** "Split `langfuse/` and `otel/` into `telemetry/tracers/*.rs` (tracer) + `telemetry/backends/*/` (client+config)."

**As-implemented:** Langfuse + OperonEyes land; OTEL is intentionally deferred per §12 ("Open questions deferred"). Cargo feature flag for `otel` is commented out in [rust/operonx/Cargo.toml](rust/operonx/Cargo.toml); ecosystem dep conflicts with OTEL's current version triangle made the port not worth blocking v0.6.0.

### 14.12 Google auth not in scope (Phase 5)

**Plan call:** "Port auth (Keycloak, Google) → `providers/auth/`."

**As-implemented:** Python-side `operonx/providers/auth/` ships **only Keycloak** — no `google.py`. The plan's "Google" mention was aspirational; the Rust port matches Python 1:1 (Keycloak only). If Google auth lands on the Python side later, the Rust port should follow.

### 14.13 crates.io publish is a separate release step

**Plan call:** "Tag operonx v0.6.0 on crates.io."

**As-implemented:** Version fields are set to `0.6.0` across the workspace; the actual `cargo publish` is a manual release action gated on crates.io account ownership and tag push. Not done by the migration work itself — left for the human release flow.
