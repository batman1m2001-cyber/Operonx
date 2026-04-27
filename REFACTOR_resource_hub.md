# Refactor Plan — Decouple ResourceHub from Operon Engine

## Why

`Operon(graph)` currently does three things at construction time: load `.env`, load `resources.yaml`, install the `ResourceHub` singleton, and then build/warmup the graph. This coupling causes four concrete problems today:

1. Pure-compute graphs (no provider ops) are forced to ship a `resources.yaml` or the engine raises `FileNotFoundError`. Hundreds of core tests rely on the project root happening to have one.
2. `_load_resources` always reads from disk and overwrites `ResourceHub._instance`. Test fixtures that pre-install a hub via `set_instance(...)` are silently clobbered on the next `Operon(...)` call.
3. The engine resolves `resources.yaml` from `Path.cwd()`. Running pytest from a subdirectory or a notebook from a different folder breaks setup.
4. When resolution fails, the user sees a generic `KeyError("Resource '...' not found in registry")` and cannot tell whether the hub was unconfigured, the file was empty, the key was missing, or an env var was unset. Each of these has a different fix.
5. Setup problems are silent until the first `get()`. A user can call `bootstrap()`, get no `resources.yaml`, and not know until much later when an op tries to resolve a name. Same for unset `${VAR}` references — they only surface when the specific resource is touched.

The refactor splits resource setup into its own layer so the engine becomes a pure orchestrator, failure messages disambiguate the setup states, and obvious setup gaps surface as warnings at bootstrap time.

## Non-goals

- No change to the public `@op` / `GraphOp` / `START` / `END` DSL.
- No change to the Rust side **in this PR**. Rust has the same coupling and will get a mirror refactor in a follow-up — see [Rust mirror (follow-up PR)](#rust-mirror-follow-up-pr) below.
- No change to provider op execution semantics. Only the resolution-failure messages improve.
- Not adding a new config format. YAML and JSON storage stay as-is.

## Public API after the refactor

### New: `operon.bootstrap()`

A free function in `operon/__init__.py`. One-line convenience for the common case.

```python
def bootstrap(
    *,
    resources: Optional[str | Path] = None,
    env: bool = True,
) -> ResourceHub | None:
    """Set up .env and ResourceHub for a typical project layout.

    - Loads .env walking up from CWD (non-override).
    - Calls ResourceHub.auto() unless `resources` is given.
    - If `resources` is given, calls ResourceHub.from_yaml(resources).
    - Returns the installed hub, or None if no resources.yaml was found.
    - Idempotent: if a hub is already installed, returns it unchanged.
    """
```

### New: `ResourceHub.auto()`

Classmethod on `ResourceHub`. Discovery + install in one call. **No walk-up.** Just checks `Path.cwd() / "resources.yaml"`. Wrappers (conftests, `examples/_common.py`) compute absolute paths themselves and pass them to `from_yaml(...)` — they don't need this convenience.

```python
@classmethod
def auto(cls) -> "ResourceHub | None":
    """Try to install ResourceHub from ./resources.yaml in CWD.

    - If a hub is already installed: return it unchanged (idempotent).
    - If ./resources.yaml exists: load, set_instance, return hub.
    - If not found: emit a warning naming the path checked, return None.

    Never raises. The warning is the early signal that setup is incomplete;
    silent miss would defer the problem to first resource resolution.
    """
```

Rationale for warning vs. silent: `auto()` (and `bootstrap()`) are *opt-in*. The user explicitly asked to set up resources — if we couldn't, we should say so. Pure-compute users who never call `bootstrap()` see no noise.

### Changed: `Operon.__init__`

Drops `_load_env` and `_load_resources` entirely. Drops the `resources=` kwarg. Pure orchestrator.

```python
def __init__(
    self,
    graph: Union[GraphOp, Callable[..., GraphOp]],
    *,
    params: Optional[Dict[str, Any]] = None,
    tracer: Optional[Union["Tracer", List["Tracer"]]] = None,
):
    """Build and prepare a graph for execution.

    Does NOT touch ResourceHub or .env. Call `operon.bootstrap()` or
    `ResourceHub.from_yaml(...)` first if your graph uses provider ops.
    """
```

`_warmup_ops` still runs eagerly. Provider ops that need the hub will raise the new disambiguated errors at this point — so failure is still fast and at construction time, just sourced from the op layer instead of the engine.

### Unchanged

- `ResourceHub.from_yaml(path)`, `from_json(path)`, `instance()`, `set_instance(hub)`, `reset_instance()`.
- `hub.get(key)`, `hub.has(key)`, `hub.register(...)`, `hub.keys()`.
- All op classes, `@op`, `@graph`.

## Warnings — early signal at bootstrap time

Two warnings fire from `bootstrap()` / `auto()` / `from_yaml()`. Both use the standard `warnings` module with a custom `ResourceHubWarning` category so users can silence them via `warnings.filterwarnings(...)` if they really want to.

| # | Trigger | Message | Where raised |
|---|---|---|---|
| W1 | `auto()` or `bootstrap()` called, no `resources.yaml` at CWD | `No resources.yaml found at <abs path>. ResourceHub not installed; provider ops will fail at resolution.` | `ResourceHub.auto` |
| W2 | `from_yaml()` loads a file that references `${VAR}` for an unset env var | `resources.yaml references unset environment variable: ${OPENAI_API_KEY} (used by 'llm:gpt-4o'). Resource will fail at resolution unless the variable is set before then.` | `YamlConfigStorage.__init__` (one-time scan after load) |

W2 is a *scan*, not lazy resolution: when `from_yaml` finishes loading the YAML into `self._raw`, walk it once collecting all `${VAR}` references, check each against `os.environ`, warn on the first missing per `(var, used-by-key)` pair. Does not fail the load — interpolation still happens lazily at `get()` time, which is where errors fire.

This means a `resources.yaml` listing ten LLMs but only OPENAI_API_KEY is set in the current `.env` will warn for the other nine at bootstrap time, even if the workflow only uses OpenAI. That's the cost of early signal; users who don't want it can scope their `resources.yaml` or filter the warning.

## Errors — five disambiguated branches

Every error message names the fix. Implemented by adding state to `ResourceHub` so `get()` can tell which branch fired.

| # | Trigger | Message | Where raised |
|---|---|---|---|
| 1 | `ResourceHub.instance()` called, none installed | `ResourceHub not configured. Call operon.bootstrap() or ResourceHub.from_yaml(<path>) before resolving resources.` | `ResourceHub.instance` |
| 2 | Hub installed but `source_path is None` (shouldn't happen in practice — `auto()` returns `None` rather than installing an empty hub — but kept for defensive completeness) | `ResourceHub has no source. Re-install via ResourceHub.from_yaml(...).` | `hub.get` when `source_path is None` |
| 3 | File loaded, key absent | `Resource 'llm:gpt-4o' not found in <source_path>.\nAvailable: <hub.keys()>.` | `hub.get` |
| 4 | Key present, `${VAR}` interpolation failed at resolve time | `Resource 'llm:gpt-4o': environment variable OPENAI_API_KEY is unset.\nLoaded from: <source_path>. .env searched: <list>.` | `YamlConfigStorage.load_one` (wrapped as `EnvVarUnsetError`) |
| 5 | Key present, env OK, factory raised | `Resource 'llm:gpt-4o' failed to initialize: <inner error>` (existing behavior, unchanged) | `hub.get` |

### State the hub needs to track

Two new attributes on `ResourceHub`:

- `source_path: Optional[Path]` — what `from_yaml` / `from_json` loaded. Used in branch (3) and (4) error messages.
- A module-level `BOOTSTRAP_ENV_PATHS: list[Path]` populated by `operon.bootstrap()` when it loads `.env`. Used in branch (4) and warning W2.

`auto()` does not install an empty hub on miss — it returns `None` and warns (W1). So we don't need a `searched_paths` attribute or an `is_empty()` method on storage.

### Env interpolation

Two-pass design: warn early, error lazily.

**Pass 1 — load-time scan (warning W2):** when `YamlConfigStorage.__init__` finishes parsing the file, walk the raw dict once, collect every `${VAR}` reference and the resource key it appears under, check each against `os.environ`, emit one `ResourceHubWarning` per missing `(var, key)` pair. No interpolation yet.

**Pass 2 — get-time interpolation (error branch 4):** when `hub.get(key)` triggers `_load_config`, the existing `${VAR}` substitution runs. If a variable is still unset, wrap the failure in `EnvVarUnsetError` (new, subclass of `KeyError` so existing `except KeyError` still catches it) with the var name, the resource key, `source_path`, and `BOOTSTRAP_ENV_PATHS`.

A user who sets `OPENAI_API_KEY` *after* `bootstrap()` but *before* `engine.run()` will see the W2 warning but no error — the warning is cheap and acceptable to false-positive in this case.

## File-by-file changes

| File | Change |
|---|---|
| [operon/__init__.py](operon/__init__.py) | Add `bootstrap()` function. Export it. Populate `BOOTSTRAP_ENV_PATHS` when `.env` is loaded. |
| [operon/core/registry/resource_hub.py](operon/core/registry/resource_hub.py) | Add `auto()` (CWD-only, warns on miss), `source_path` attribute. Update `get()` with disambiguated branches. Update `instance()` error message. Define `ResourceHubWarning` and `EnvVarUnsetError`. |
| [operon/core/registry/storage.py](operon/core/registry/storage.py) | Scan raw YAML/JSON for `${VAR}` references at `__init__`, emit W2 warnings for unset ones. Wrap env interpolation in `load_one` to raise `EnvVarUnsetError` on miss. |
| [operon/core/registry/__init__.py](operon/core/registry/__init__.py) | Export `EnvVarUnsetError`, `ResourceHubWarning`. |
| [operon/core/engine.py](operon/core/engine.py) | Remove `_load_env`, `_load_resources`, `resources=` kwarg. Drop `resources` slot. Update docstring. |
| [examples/python/_common.py](examples/python/_common.py) | Replace `if RESOURCES_FILE.exists()` block with `operon.bootstrap(resources=str(RESOURCES_FILE))` once at module level (or skip when file is absent). |
| [tests/internal/providers/conftest.py](tests/internal/providers/conftest.py) | Keep `setup_resource_hub` fixture — it is now *authoritative* (engine no longer clobbers it). |
| [tests/internal/core/conftest.py](tests/internal/core/conftest.py) | No change — pure-compute graphs no longer require a hub. |
| [tests/spec/conftest.py](tests/spec/conftest.py) | No change — same as above. |
| [tests/internal/telemetry/conftest.py](tests/internal/telemetry/conftest.py) | No change. Tracer tests already mock `ResourceHub.instance`. |

## Backwards compatibility

This is a **breaking change** at the API surface, but the surface is small and the migration is mechanical:

- Anyone calling `Operon(graph, resources='...')` must switch to `operon.bootstrap(resources='...')` followed by `Operon(graph)`. There is no shim — the kwarg is removed.
- Anyone relying on `Operon(graph)` to auto-load `.env` and `resources.yaml` from CWD must add a `operon.bootstrap()` call before it. Without that, pure-compute graphs still work; provider graphs raise the new branch (1) error.

The error message in branch (1) names the exact fix, so the migration cost for downstream code is one error → one line change.

Examples and tests in this repo will be updated in the same PR.

## Test plan

New tests in `tests/internal/core/registry/test_resource_hub.py`:

- `test_auto_finds_yaml_in_cwd` — `chdir` into a tmpdir with `resources.yaml`; `auto()` finds it, `source_path` is set, no warning.
- `test_auto_warns_when_missing` — `chdir` into empty tmpdir; `auto()` returns `None`, no instance installed, **emits `ResourceHubWarning`** (W1) naming the absolute path checked.
- `test_auto_idempotent_when_hub_already_set` — pre-install hub via `set_instance`, call `auto()`, assert the original hub is returned and `_instance` unchanged. No warning.
- `test_from_yaml_warns_on_unset_env_vars` — load YAML referencing `${UNSET_VAR}`; **emits `ResourceHubWarning`** (W2) once per `(var, key)` pair. Setting the var afterwards does not retroactively suppress the warning.
- `test_from_yaml_no_warning_when_env_vars_set` — load YAML referencing `${SET_VAR}` with the var present; no warning.
- `test_get_branch_3_key_missing` — load file with `llm:a`; `get("llm:b")` raises with `Available: ['llm:a']` and source path.
- `test_get_branch_4_env_unset` — YAML references `${MISSING}`; `get(...)` raises `EnvVarUnsetError` naming `MISSING`, source path, and `.env` search paths.
- `test_get_branch_5_factory_failure` — unchanged from today; verify message format hasn't regressed.

New tests in `tests/internal/core/test_workflow.py`:

- `test_engine_no_resources_yaml` — pure-compute graph, no `resources.yaml` anywhere, `Operon(graph).run(...)` succeeds.
- `test_engine_does_not_clobber_existing_hub` — pre-install hub A, construct `Operon(graph)`, verify `ResourceHub.instance() is hub_A`.

Update existing tests:

- Any test that passes `Operon(graph, resources='...')` becomes `operon.bootstrap(resources='...'); Operon(graph)`.
- Any test that runs a provider graph without a fixture-installed hub gains an explicit setup call. Today most providers tests rely on `setup_resource_hub` autouse, which is fine.

## Migration steps (suggested order)

1. **Add new API in parallel.** Implement `auto()`, `bootstrap()`, the new error branches, and the state-tracking attributes on `ResourceHub`. No removals yet. All existing tests still pass.
2. **Update engine.** Remove `_load_env` / `_load_resources` from `Operon.__init__`. Update docstring. Drop `resources=` kwarg. At this point examples and many core tests will fail (they don't have `resources.yaml` at CWD anymore).
3. **Update examples and tests.**
   - `_common.py` calls `bootstrap()` once at module import.
   - Provider conftests are authoritative now — verify no test relies on engine reloading the hub.
   - Core tests that don't touch providers need no change (graph-only construction now works hub-free).
4. **Update docs.** [CLAUDE.md](CLAUDE.md) "Resource setup" section, [README.md](README.md) quickstart, any guide referencing `Operon(graph, resources=...)`.
5. **Run full test suite + every example end-to-end** before merge.

Each step is a separate commit so rollback is granular.

## Rust mirror (follow-up PR)

Rust has the same coupling. [`Operon::new(graph_json)`](rust/operonx/src/core/engine.rs#L660) auto-loads `.env` + `resources.yaml` from CWD and hard-fails when the file is missing. The builder already has partial escape hatches — `.no_resources()` ([line 1007](rust/operonx/src/core/engine.rs#L1007)) installs `ResourceHub::empty()`, and `.resources(path)` overrides — but the default still traps users. There's no warning system, no `source_path` tracking, no `${VAR}` scan, no disambiguated error variants.

**Phasing decision: Python first, Rust mirror in a separate PR.** Reasons:

1. Doubling surface area in one PR makes review painful — Python alone touches ~7 files + tests; Rust adds another 5+ files with different idioms (`tracing::warn!` vs Python `warnings`, `OperonError::EnvVarUnset` enum variant vs exception class).
2. The Rust bug is less acute today because `.no_resources()` exists — anyone hitting the trap has a documented workaround.
3. Getting the design right in Python first means we copy a known-good pattern into Rust, rather than designing both at once and risking divergence.
4. [MIGRATION_rust.md](MIGRATION_rust.md) is 1921 lines and tracks Rust ports under its own discipline. The Rust changes will live there as a new section, not entangled with this Python plan.

**Concrete Rust changes for the follow-up PR:**

| File | Change |
|---|---|
| `rust/operonx/src/core/engine.rs` | Flip `require_resources_file` default to `false` in `OperonBuilder`. `Operon::new(json)` becomes a thin wrapper that calls `.bootstrap()` (mirrors Python). Missing file → `tracing::warn!` + empty hub, not error. |
| `rust/operonx/src/core/registry/resource_hub.rs` | Add `ResourceHub::auto()` returning `Option<Arc<ResourceHub>>` with `tracing::warn!` on miss. Add `source_path: Option<PathBuf>` field. |
| `rust/operonx/src/core/registry/storage.rs` (or wherever YAML loading lives) | Scan parsed YAML for `${VAR}` references at `from_yaml` time, emit `tracing::warn!` for unset ones. Wrap interpolation failures as `OperonError::EnvVarUnset { var, key, source_path, env_paths }`. |
| `rust/operonx/src/core/exceptions.rs` | Add `OperonError::EnvVarUnset { var: String, key: String, source_path: Option<PathBuf>, env_paths: Vec<PathBuf> }` variant. |
| `rust/operonx/src/lib.rs` | Add free `bootstrap()` function mirroring Python's. |
| `rust/operonx/tests/` | Mirror the Python warning/error test cases. |

**Parity invariants** the Rust mirror must preserve:

- `Operon::new(graph_json)` works for pure-compute graphs with no `resources.yaml` and no `.env` anywhere (no error, no panic; one `tracing::warn!` at most).
- A pre-installed hub via `ResourceHub::set_instance(...)` is **not** clobbered by subsequent `Operon::new` / `OperonBuilder::build` calls.
- The five error branches and two warnings have the same semantics as Python; only the error type and warning channel differ (`OperonError` enum + `tracing` vs Python exception + `warnings` module).

**Out of scope for the Rust mirror PR:** any change to Rust provider ops, plugin loader, or telemetry backends. Pure resource-setup decoupling only — same as Python.

## Open questions

1. **Should `bootstrap()` be implicit on `import operon`?** Recommendation: no. Explicit setup is cheaper than the surprise factor of import side-effects, especially in notebooks and embedded use cases. One extra line.
2. ~~Should `auto()` walk up?~~ **Resolved: no.** CWD-only. Wrappers (conftests, examples) already compute absolute paths in Python and pass them to `from_yaml(...)`; the hub's contract should not include filesystem traversal. Walk-up risks picking up an unrelated parent project's `resources.yaml`.
3. **Should env-var problems be warning + lazy error, or eager error?** Resolved: warning at load time (W2) + lazy error at `get()` time (branch 4). A user who sets the var between `bootstrap()` and `engine.run()` should not be blocked by an over-eager check.
4. **Do we keep `Operon(graph, tracer=...)` or move tracer construction out too?** Out of scope for this refactor. Tracer is per-engine, not global state, so the coupling is correct.
5. **Warning category — `ResourceHubWarning` or `UserWarning`?** Recommendation: a dedicated `ResourceHubWarning(UserWarning)` subclass so users can silence W1/W2 without muting all `UserWarning` traffic in their app.
6. ~~Should the Rust side change too?~~ **Resolved: yes, but in a separate PR.** See [Rust mirror (follow-up PR)](#rust-mirror-follow-up-pr).

## Acceptance criteria

- `Operon(pure_compute_graph)` works with no `resources.yaml` and no `.env` anywhere. No warnings fire.
- `Operon(provider_graph)` without prior `bootstrap()` raises a branch-(1) error that names `operon.bootstrap()` as the fix.
- `bootstrap()` with no `resources.yaml` at CWD emits W1 once, returns `None`, installs no hub.
- `from_yaml(...)` on a file referencing unset `${VAR}` emits W2 once per `(var, key)` pair, still returns a usable hub.
- `set_instance(hub_A)` followed by `Operon(any_graph)` leaves `hub_A` in place.
- All five failure branches and both warnings have distinct, fix-pointing messages and direct test coverage.
- Provider integration tests pass without modification (their conftest is authoritative).
- Examples run end-to-end when invoked from project root (the [_common.py](examples/python/_common.py) wrapper computes absolute paths; CWD-elsewhere is not a guaranteed-to-work case for examples).
