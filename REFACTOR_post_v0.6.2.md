# Refactor — Post-v0.6.2 Polish

Scratch plan for the work between v0.6.2 (first fully-published release) and
the next minor. Three workstreams: examples, docs, coverage. Delete this
file once every item below is checked off.

Anchors:
- Live versions — PyPI [`operonx 0.6.2`](https://pypi.org/project/operonx/0.6.2/),
  crates.io [`operonx`](https://crates.io/crates/operonx) +
  [`operonx-macros`](https://crates.io/crates/operonx-macros), tag `v0.6.2`.
- Coverage today — 57 % (`8525` statements, `3646` missed). Provider modules
  that need live API credentials sit at 0 % because the 227 integration tests
  are deselected in CI; this drags the headline number down even though the
  unit-testable surface is much better covered.
- Docs today — 21 markdown pages, ~1400 lines. Layout is fine, content is
  thin: no diagrams anywhere in `docs/architecture/`, landing page is bare,
  one stale reference (`ForOp/MapOp/WhileOp` in `streaming.md`, framed as
  history — leave it).

---

## P1 — Install layering + standalone example projects

Two coupled refactors. The dep layering must land first; the example
templates depend on it being clean.

### P1.A — Install layering (do first)

Today `pip install operonx` drags in `openai`, `aiohttp`, `httpx`
(~30 MB) because the LLM type system uses `openai.types.chat.*` as the
canonical message format. A user trying ex01 (pure compute) gets the
whole provider stack. Same on the Rust side — `cargo add operonx`
compiles every provider feature by default. Fix this so each tier of
install is honest about what it covers.

**Target tiers (Python):**

| Tier | Install | Pulls in | Examples that fit |
|---|---|---|---|
| 1 — Core | `pip install operonx` | engine, ops DSL, state, registry, telemetry-base. **No** providers. ~5 MB. | ex01, ex02, ex13 |
| 2 — Single provider | `operonx[openai]` / `[anthropic]` / `[gemini]` / `[bedrock]` | tier 1 + that one SDK | ex03, ex04 (single-provider variants) |
| 3 — Feature extra | `operonx[langfuse]` / `[otel]` / `[serve]` / `[onnx]` / `[huggingface]` | additive on top of 1 or 2 | ex07 (`[onnx]`), ex14 (`[langfuse]`) |
| 4 — Meta | `operonx[standard]` (= `[openai,langfuse,otel,serve]`), `operonx[all]` | pre-bundled common combos | dev / kitchen-sink |

**Target tiers (Rust):** same idea via Cargo features —
`operonx = { version = "0.6.x", default-features = false }` is tier 1;
`features = ["openai"]` etc. layer providers on; `features = ["all"]`
matches Python's `[all]`.

**Steps — Python (DONE):**

- [x] Move `openai`, `aiohttp`, `httpx` out of `[project] dependencies`
      in [`pyproject.toml`](pyproject.toml). Add a missing `[openai]`
      extra. Update the install-tier comment block to spell out the
      four tiers explicitly.
- [x] Verify clean tier-1 install: `pip install operonx` in a fresh
      venv pulls only `pydantic`, `pyyaml`, `rich`, `orjson`,
      `pydantic-core`, `pygments`, `typing-extensions`,
      `typing-inspection`. No provider SDK in the dep tree.
- [x] Verify `from operonx.core import Operon; from operonx.core
      import op, GraphOp, START, END, PARENT` works from that venv,
      and that running a pure-compute graph end-to-end succeeds.
      Also confirmed `'openai' not in sys.modules` after the import.
- [x] Verify the existing 848 unit tests still pass under the
      `--all-extras` dev install.

**Steps — Python (deferred, partial):**

The recon showed `operonx.core` was already free of provider SDK
imports — the heavy install was purely a `pyproject.toml` declaration
issue, not an import-graph one. Tier 1 ships clean. The deeper cleanup
below would also tighten `import operonx.providers`. Track for a
future minor.

- [x] Finish lazy provider exports — `_LAZY_BACKENDS` now covers the
      full public surface in `operonx/providers/__init__.py` (configs
      + factories + base classes + ops + heavy backends). The eager
      `from operonx.providers.auth/embeddings/llms/ops/rerankers
      import …` lines are gone. `import operonx.providers` on a
      tier-1 install no longer pulls `httpx` / `openai` / `numpy`.
      `auth/factory.py` was the one factory that eagerly imported a
      heavy backend (`keycloak.py` → `httpx`); fixed by deferring the
      `from .keycloak import KeycloakTokenProvider` to inside
      `create_auth()` with a typed missing-dep ImportError.
- [x] Define `operonx.core.types.ChatMessage` (TypedDict) — landed
      with `ChatRole` Literal + a TypedDict body in
      `operonx/core/types/chat.py`. No converter at the LLMOp
      boundary yet (still emitting `openai.types.chat.ChatCompletion`
      for backwards compat); this just gives the converter layer a
      target type to land in v0.7.

**Steps — Rust (deferred to v0.7):**

The Rust side has the same shape today (`cargo add operonx` defaults
to `langfuse + operon_eyes`, every provider module is always
compiled), but feature-gating it properly cascades:

- `reqwest` is shared by providers, the Langfuse client, and the
  `operon_eyes` tracer — making it optional means feature-flagging
  three subsystems, not just one.
- Every `pub mod openai;` / `anthropic;` / `gemini;` in
  `rust/operonx/src/providers/llms/mod.rs` (and embeddings/, rerankers/)
  needs an accompanying `#[cfg(feature = "...")]` and so does each
  re-export. `factory.rs` would dispatch via feature-gated arms.

That is a real refactor with breaking-change risk on the public API.
Defer to v0.7. For now, Rust users see the same "install everything"
experience they had at 0.6.1; the Python side is the visible win for
this release.

- [ ] **v0.7 task** — Cargo feature gating: per-provider features
      (`openai`, `anthropic`, `gemini`, `bedrock`, plus `embeddings`
      and `rerankers` aggregates), `default = []`, aggregate features
      mirroring Python's `[providers]`/`[standard]`/`[all]`. Make
      `reqwest` optional. CI pass: `cargo build --no-default-features`
      compiles with no provider crates pulled.

### P1.B — Per-example projects (single file by default)

Each `examples/{python,rust}/exNN_*/` becomes a self-contained project a
user can copy out as a starting template. **Single `main.py` / `main.rs`
per example by default** — ops, graph builder, and entry point in one
file, top to bottom. Splitting into `workflow.py` + `demo.py` (or
`graph.rs` + `main.rs`) is reserved for examples whose flow is
genuinely complex enough that the split clarifies (ex09 agent, ex12
advanced RAG, ex15 callbot streaming — confirm per-example as we go).

Some duplication across examples is intentional — the examples teach
project structure, not just API usage.

The shared bench machinery (Python `BenchReporter` + `bench_results/` +
`_dump_graph.py`; Rust `_common.rs` reporter) is removed in this pass.
A separate, better bench will replace it later — out of scope here.

### Python layout per example

```
examples/python/ex03_llm_chat/
├── pyproject.toml         # name = "operonx-ex03-llm-chat"
│                          # dependencies = ["operonx[anthropic]>=0.6.x"]
├── README.md              # what it teaches, how to install, how to run
├── .env.example           # only the keys this example needs
├── resources.yaml         # only the resources this example uses (if any)
├── main.py                # ops + graph builder + asyncio.run, top-to-bottom
└── inputs.json            # illustrative input
```

### Rust layout per example

```
examples/rust/ex03_llm_chat/
├── Cargo.toml             # name = "operonx-ex03-llm-chat"
│                          # operonx = { version = "0.6.x", features = ["..."] }
├── README.md
├── .env.example
├── resources.yaml
├── src/
│   └── main.rs            # #[op] declarations + run loop, single file
└── inputs.json
```

Each Rust example is its own crate, **not** a workspace member of `rust/`.
A committed `examples/rust/.cargo/config.toml` adds
`[patch.crates-io] operonx = { path = "../../rust/operonx" }` so engine
devs running `cargo run` from any example dir build against the local
workspace, not crates.io. Users copying an example out of the repo drop
that file along with the rest of the parent dir and the registry version
takes over.

Examples with no API deps (ex01, ex02, ex13) skip `.env.example` and
`resources.yaml` and depend on tier-1 (`operonx` / `operonx with
default-features = false`). Examples needing a single provider depend
only on that extra/feature so each demo proves a real install slice.

### Steps — Python

- [x] **Delete** the umbrella `examples/python/pyproject.toml`,
      `examples/python/_common.py`, `examples/python/_dump_graph.py`,
      `examples/python/__init__.py`, and `examples/bench_results/`.
- [x] **For each `exNN_*/`** add `pyproject.toml` (minimal deps for that
      example), `README.md`, and where it makes sense `.env.example` +
      `resources.yaml`.
- [x] **Collapse `workflow.py` + `demo.py` → `main.py`** for every
      example whose flow fits comfortably in one file. Default is
      single file; split only where complexity demands it.
- [x] **Strip bench reporting from each entry point**: removed
      `BenchReporter`, `Scenario`, the `--runs`/`--warmup` CLI, and the
      `sys.path.insert(REPO_ROOT)` hack. Each example is now a clean
      `asyncio.run(main())` calling `engine.run(...)`.
- [x] **Re-do ex01**: collapsed to a single `main.py` against tier-1.

### Steps — Rust

- [x] **Drop** every `[[example]]` entry from
      `rust/operonx/Cargo.toml` (the umbrella workspace pointer is gone).
- [x] **Delete** `examples/rust/_common.rs`.
- [x] **For each `exNN_*/`** add `Cargo.toml` (depends on `operonx`
      from crates.io with the right feature subset), move `demo.rs` →
      `src/main.rs`, add `README.md`, and where relevant `.env.example`
      + `resources.yaml`.
- [x] **Rewrite each `main.rs` as single file**: dropped the
      `#[path = "../_common.rs"]` include and bench loop. Each example
      is `#[op]` declarations + a plain `main()` that loads inputs,
      runs once, prints.
- [x] **Add** `examples/rust/.cargo/config.toml` with the
      `[patch.crates-io]` override so workspace dev still wins over
      the registry version.

### Side-quests surfaced while doing P1

- [x] **Rust scheduler inline fast path for sync ops** — landed in
      [`task_scheduler.rs::spawn_op`](rust/operonx/src/core/ops/graph/task_scheduler.rs).
      Sync ops (`bound: Sync`) now run inline: no `tokio::spawn`, no
      semaphore acquire, and the events go onto the queue via
      `try_send` (queue bumped 256 → 8192 to absorb inline bursts).
      Rust is now consistently 2–3× faster than Python on every
      pattern in `scripts/bench/`:

      | Pattern | Rust before | Rust after | Python | Rust speedup |
      |---|---|---|---|---|
      | linear_50 | 2.09 ms | 0.93 ms | 2.69 ms | 2.9× |
      | linear_500 | 21.94 ms | 7.69 ms | 15.93 ms | 2.1× |
      | fib_chain_20x500 | 1.08 ms | 0.62 ms | 1.70 ms | 2.7× |

      Per-op floor dropped from ~44 µs to ~15 µs. Still ~10× behind
      Hush-ai's mature scheduler — closing that remaining gap (skip
      the channel entirely for sync ops, recursive on_frame call,
      rayon for cpu-bound) is a follow-up.
- [x] **Rust nested `@graph` (`OpType::Graph`) dispatch** — landed in
      [`task_scheduler.rs::execute_op`](rust/operonx/src/core/ops/graph/task_scheduler.rs).
      `OpType::Graph` now recursively builds a sub-`Operon` (cached
      process-wide by `op_cfg.full_name` so the build cost is paid
      once per nested config, not per call). The sub-engine
      `run_json_async`s with the resolved inputs and returns its
      outputs as the parent op's frame value. `nested_{2,5,10}`
      patterns produce real outputs.
- [x] **Rust nested `@graph` precompute + fast-path dispatch** —
      landed. Two-part fix shipped:
      1. `GraphScheduler::new` now recursively builds a child
         `GraphScheduler` for every nested `OpType::Graph` op
         (`build_child_schedulers` in
         [`task_scheduler.rs`](rust/operonx/src/core/ops/graph/task_scheduler.rs)),
         keyed by the child's `full_name` and stored on the parent
         scheduler in `Arc<HashMap<String, Arc<GraphScheduler>>>`.
         Sub-schedulers share the parent's `OpRegistry`. The
         process-wide `static NESTED_ENGINE_CACHE` is gone.
      2. `GraphScheduler::run_collect(inputs)` is the inline
         fast-path: builds a `FrameSender::tap_only(tap)`, calls
         `Scheduler::run(self, ...)` directly in the caller's task,
         then aggregates the captured frames' `data` maps into the
         result `Value`. No `tokio::spawn`, no
         `mpsc::channel(64)` allocation, no `pump_loop`, no UUID
         gen, no middleware. The `OpType::Graph` arm in `execute_op`
         is a single map lookup + this `run_collect` call.
      Mirrors Python's `child._scheduler.run(state, ctx)` shape.
      Bench results (after reverting the `bench_fib(2000)`
      workaround in `inner_pipeline` so nested ops are pure noops
      again):
      - `nested_2`:  Python 1.21 ms vs Rust **0.96 ms** (1.3× faster)
      - `nested_5`:  Python 2.51 ms vs Rust **1.72 ms** (1.5× faster)
      - `nested_10`: Python 4.55 ms vs Rust **2.95 ms** (1.5× faster)
      - `production_3`: 7.8× → **10.3×** (every nested `verify_case`
        now takes the fast-path)
      - `production_5`: 9.1× → **11.0×**
- [x] **Rust `BranchOp` config fields** — added `cases`, `default`,
      `candidates` (and a new `BranchCase` struct) to
      [`rust/operonx/src/core/configs/op_config.rs`](rust/operonx/src/core/configs/op_config.rs).
      JSON for `branching_*` and `production_*` deserialises cleanly
      now. Branch ops still don't *route* selectively at runtime
      (see next bullet), but the bench runs and outputs match Python
      because every case target fires and the soft-edge merge picks
      the answer.
- [x] **Rust `if_()` branch routing dispatch** — landed. Two phases:
      1. **Ref-transform evaluator** in
         [`task_scheduler.rs::resolve_ref`](rust/operonx/src/core/ops/graph/task_scheduler.rs).
         Walks `RefConfig.transforms` and applies each via
         `apply_transform`. Coverage: comparison (`eq` / `ne` / `lt`
         / `le` / `gt` / `ge`), `contains`, access (`getitem` /
         `getattr` — same handler for objects), boolean (`and_` /
         `or_` / `not_` with short-circuit + nested-Ref operand
         resolution via `RefArg::NestedRef`), arithmetic (`add` /
         `sub` / `mul` / `truediv` / `floordiv` / `mod` / `pow`
         and r-variants on f64), unary (`neg` / `pos` / `abs`).
         Truthiness rules match Python (`null` / `false` / `0` /
         `""` / `[]` / `{}` falsy). Not implemented: `apply` /
         `call` (Python-callable specific), `matmul` /
         `rmatmul` (numpy-only).
      2. **`OpType::Branch` dispatch** in `spawn_op`'s inline
         fast-path. New `evaluate_branch` helper walks
         `op_cfg.cases`, resolves each condition Ref through the new
         evaluator, returns the first truthy `target` (or `default`,
         or a typed error). The result map carries
         `{"__branch_target__": "<name>"}`, and the existing
         scheduler edge router already filters out non-matching
         edges when that key is present.
      Result on the bench:
      - `branching_5`:  0.91 ms → **0.57 ms** (1.6× faster — only the
        chosen branch fires now, not all four)
      - `branching_10`: 1.58 ms → **0.96 ms** (1.6× faster)
      - `production_*`: marginal speedup (each `verify_case` no
        longer fires both pass + fail)
      Output values previously matched Python by coincidence (every
      branch fired and the soft-edge merge picked one); now Rust is
      semantically correct and would not diverge on a graph where
      different branches produce different results.
- [x] **CPU-bound bench shows real Rust speedup** — added
      `bench_matrix(size)` (naive O(n³) matmul, no library shortcut)
      and `matrix_chain_*` patterns to `scripts/bench/`. Headline:
      `matrix_chain_5x100` runs at **616 ms in Python vs 17.8 ms in
      Rust = 34.7× faster**. The 5x60 / 10x40 variants land at
      27× / 23× respectively. **Dropped `cpu_chain_*` (hash chain)
      and `bench_hash` op** — `hashlib.sha256` is OpenSSL C and `sha2`
      is pure Rust, so a hash-chain bench measured the hash library
      not the engine; `matrix_chain_*` covers CPU-chain stress fairly.
- [x] **`cpu_contention_*` switched to matmul** — same hashlib bias
      affected `cpu_contention_*h_*l_5000i` (heavy branches were hash
      chains). Replaced with `bench_matrix(size=30)` heavy branches —
      patterns now `cpu_contention_{3h_10l,5h_10l}_30m`. Result: Rust
      went from 0.82-0.83× (slower) to **14-17× faster**.
- [x] **`production_*` made compute-realistic** — `verify_case`
      previously had a trailing `bench_noop` after `if_()` routing,
      so each nested @graph dispatched ~5 trivial ops and the sub-
      engine setup overhead dominated. Replaced trailing noop with
      `bench_matrix(size=30)`. Result: Rust went from parity (0.94-
      0.99×) to **7.8-9.1× faster**.
- [x] **`__version__` source of truth**: `operonx/__init__.py` now
      reads `importlib.metadata.version("operonx")` with a
      `PackageNotFoundError` fallback to `"0.0.0+unknown"`. Editing the
      version in `pyproject.toml` is now the single source of truth.
- [x] **`#[op]` macro hygiene**: `operonx` now re-exports `inventory`
      via `pub use ::inventory;`, and both `#[op]` and `#[resource]`
      macros emit `::operonx::inventory::submit!` instead of bare
      `::inventory::submit!`. Dropped `inventory = "0.3"` from every
      example crate's `Cargo.toml` and from `scripts/bench/Cargo.toml`
      — verified `cd scripts/bench && cargo build --release` and
      `cd examples/rust/ex01_hello_world && cargo build --release`
      both succeed without the direct dep.

### Steps — shared

- [x] **Top-level `examples/README.md`** — landed (103-line index with
      per-example tier mapping, install tiers table, Rust dev-mode
      override section, runtime parity caveats).
- [x] **Refresh runtime-parity caveats in `examples/README.md`** —
      Nested `@graph` moved out of the "known gaps" list into
      "recently closed". The `if_()` bullet now spells out that
      `cases` / `default` / `candidates` deserialise but the scheduler
      fires every case target (real selective routing blocked on the
      ref-transform evaluator).
- [x] **Per-language indexes** `examples/python/README.md` +
      `examples/rust/README.md` — landed. Each maps every example to
      its install (extras for Python, features for Rust), spells out
      the cd-and-run command, and (Rust) carries the runtime-status
      caveats per example. Top-level `examples/README.md` points down
      to these.
- [x] **`tools/dump-graph.py` update** — rewritten for the
      `main.py` layout. Imports `examples.python.{ex}.main`, calls
      each `@graph` factory with `None` for every parameter (which
      becomes a PARENT external-input ref), runs `operonx.bootstrap()`
      from inside the example dir so provider ops resolve `resources.
      yaml`. Each scenario gets its own top-level GraphOp `name` so
      the bundled output mirrors the previous shape.
      `dump_graph(factory, scenario)` is the new core. **Note:**
      regenerating overwrites the checked-in `graph.json` and changes
      `full_name` / op naming relative to the v0.6.2 emit; per-example
      regen is opt-in by the example author.
- [x] **Elevate `dump-graph` to a packaged CLI** — landed as
      `operonx-pack`. New module
      [`operonx/tools/pack.py`](operonx/tools/pack.py) +
      `[project.scripts] operonx-pack = "operonx.tools.pack:main"`
      in [`pyproject.toml`](pyproject.toml). Targets are pytest-style
      `module::symbol` positionals. Default to stdout JSON; `-o PATH`
      writes to file. Bundle key = symbol name when multiple targets
      are passed; single target dumps the spec at top level. Auto
      `operonx.bootstrap()` from CWD; `--no-bootstrap` flag skips it
      for pure-compute graphs. The old standalone `tools/dump-graph.py`
      is gone (and the `tools/` directory with it).
      ```bash
      cd examples/python/ex03_llm_chat
      operonx-pack main::basic_chat -o ../../rust/ex03_llm_chat/graph.json
      operonx-pack m::a m::b m::c -o bundle.json
      operonx-pack --no-bootstrap m::pure_compute   # tier-1, no resources.yaml
      ```
      Explicitly **not** in this pass: subcommand hub (`operonx graph
      dump …`), literal-value param flags (`--params x=5`).
- [ ] **Regenerate example `graph.json` files** with the new CLI
      (depends on the CLI landing). Today's checked-in
      `examples/rust/exNN_*/graph.json` files predate the
      example-refactor and carry stale `full_name` / op naming. Per
      example: `cd examples/python/exNN && operonx-dump-graph
      main::<factory> -o ../../rust/exNN/graph.json`. Each Rust
      example needs to be rerun afterward to verify the new graph
      shape still produces the expected output.
- [x] **CI extras-smoke matrix** — landed in
      [`.github/workflows/tests.yaml`](.github/workflows/tests.yaml)
      `extras-smoke` job (anthropic / langfuse / otel / onnx / serve /
      standard / all). Catches missing dep declarations.
- [x] **CI `published-smoke` job** — landed in
      `.github/workflows/published-smoke.yaml`. Triggers on
      `workflow_dispatch`, `workflow_run` after `Publish` succeeds,
      and `push` to `main` (when example or workflow files change).
      Matrix over the three tier-1 examples (ex01, ex02, ex13): fresh
      `uv venv`, `uv pip install -e examples/python/exNN_*`, asserts
      `operonx` resolves to site-packages and `openai`/`httpx`/
      `aiohttp` are absent from `pip list`, then runs `python main.py`
      with a 60 s timeout. Does NOT gate `publish.yaml`.

---

## P2 — Docs depth pass

Goal: make `docs/architecture/` actually explain the system instead of
sketching it. Add diagrams, expand the landing page, and pull the
op-authoring patterns out of CLAUDE.md into a real doc page so external
contributors see them.

- [x] **docs/index.md** — has tagline, "Why Operonx" feature grid, and a
      30-second `@op` + `>>` quick-start. Still missing the "When to use
      Operonx vs X" comparison table (vs Airflow, vs Prefect, vs
      LangGraph) — track separately if needed.
- [ ] **docs/architecture/overview.md**: add a mermaid diagram showing
      `Operon` → `GraphOp` → `Op` → `State` → `Tracer`. Include the
      Python ↔ Rust runtime split.
- [ ] **docs/architecture/execution-flow.md**: add a sequence diagram for
      a 3-op linear graph: build → schedule → run → record. Annotate which
      steps are pre-engine (untimed) vs in `engine.run()`.
- [ ] **docs/architecture/state-model.md**: add a diagram of `MemoryState`
      cells, `PARENT` references, and the parent ↔ child boundary in
      nested `GraphOp`s. Spell out the "use `op[\"key\"]` for siblings,
      `PARENT[\"key\"]` for external inputs" rule that today only lives
      in CLAUDE.md.
- [ ] **docs/architecture/streaming.md**: add the streaming-scheduler
      timing diagram (yield → downstream parallel run). Expand the
      generator-op vs `GraphOp.loop` distinction with one concrete
      example each.
- [ ] **docs/architecture/rust-python.md**: diagram of the Rust runtime's
      `inventory!` registry, the JSON-spec round-trip, and the parity
      contract enforced by `tests/spec/`.
- [ ] **docs/guide/ — new page** `docs/guide/00b-patterns.md` (or fold
      into `01-first-workflow.md`): the @op / @graph / `>>` / `>>~` /
      PARENT / `Op.of()` / loop patterns currently in CLAUDE.md
      "Key Patterns". Internal contributors get them from CLAUDE.md;
      external readers should not have to grep that file.
- [x] **mkdocs.yml** — `pymdownx.superfences` is already in
      `markdown_extensions`. Mermaid blocks will render once the
      diagrams above are added.

Skip: theme overhaul, custom CSS, switching docs framework. Material
defaults are fine.

---

## P3 — Coverage (decision: leave at 57 %)

The 57 % badge is honest. Integration-heavy provider code (LLMs, rerankers,
ONNX, Triton) is 0 % in the unit-test lane by design — those paths need
live API credentials and run as nightly / manual integration tests, not on
every push. Chasing the badge with mocks would be busywork that proves
little; reframing the number with `omit =` would be cosmetic. Leave it.

If a future contributor finds the badge misleading, revisit then.

---

## Done-when

- [x] **Python tier 1 lean**: `pip install operonx` in a clean env does
      not install `openai`/`aiohttp`/`httpx`; `from operonx.core import
      Operon` works and `sys.modules` carries no provider SDK. The
      `import operonx.providers` path also stays lean now.
- [ ] **Rust tier 1 lean**: deferred to v0.7 — see P1.A "Steps — Rust".
- [x] **Each `examples/{python,rust}/exNN_*/` is a standalone project**
      with one entry-point file and a manifest pinning the right tier.
      Per-language `README.md` indexes also landed.
- [ ] `published-smoke` CI green on `main` — workflow added; needs the
      first publish run to verify it works against the registry wheel.
- [ ] `docs/architecture/` pages each carry at least one diagram.
- [ ] This file deleted in the same PR as the last P-item lands.
