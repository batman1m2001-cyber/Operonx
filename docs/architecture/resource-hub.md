# Resource hub

`ResourceHub` is the singleton that resolves names like `resource="gpt-4o"`
into concrete provider configurations (API keys, endpoints, model params).
This page is the **authoritative reference** for how setup works, what
warnings and errors mean, and how to integrate the hub into tests and
multi-config projects.

## Why setup is decoupled from the engine

Earlier versions of the engine auto-loaded `.env` and `resources.yaml`
from CWD inside `Operon(graph)`. That coupling caused four problems:

1. **Pure-compute graphs were forced to ship a `resources.yaml`** or the
   engine raised `FileNotFoundError`.
2. **`Operon(graph)` clobbered a pre-installed hub**, breaking tests that
   pre-installed a fixture hub via `set_instance(...)`.
3. **CWD-relative resolution** broke pytest from a subdirectory and
   notebooks from a different folder.
4. **Generic `KeyError` on resolve failure** told the user nothing about
   whether the hub was unconfigured, the file empty, the key missing, or
   an env var unset — each has a different fix.

The current design splits resource setup into its own layer. The engine
is a pure orchestrator. Setup is **explicit** via `operonx.bootstrap()`.

## Public API

### `operonx.bootstrap()`

One-line setup for the common case.

```python
def bootstrap(
    *,
    resources: Optional[str | Path] = None,
    env: bool = True,
) -> Optional[ResourceHub]:
    """Set up .env and ResourceHub for a typical project layout.

    - Loads .env from CWD (non-override).
    - Calls ResourceHub.auto() unless `resources` is given.
    - If `resources` is given, calls ResourceHub.from_yaml(resources).
    - Returns the installed hub, or None if no resources.yaml was found.
    - Idempotent: if a hub is already installed, returns it unchanged.
    """
```

### `ResourceHub.auto()`

Discovery + install in one call. **No walk-up.** Just checks
`Path.cwd() / "resources.yaml"`. Wrappers (conftests, `examples/_common.py`)
compute absolute paths themselves and pass them to `from_yaml(...)` —
they don't need this convenience.

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

### `Operon.__init__`

Pure orchestrator. Drops `_load_env`, `_load_resources`, and the
`resources=` kwarg.

```python
def __init__(
    self,
    graph: Union[GraphOp, Callable[..., GraphOp]],
    *,
    params: Optional[Dict[str, Any]] = None,
    tracer: Optional[Union["Tracer", List["Tracer"]]] = None,
):
    """Build and prepare a graph for execution.

    Does NOT touch ResourceHub or .env. Call `operonx.bootstrap()` or
    `ResourceHub.from_yaml(...)` first if your graph uses provider ops.
    """
```

`_warmup_ops` still runs eagerly. Provider ops that need the hub raise
disambiguated errors at construction time — failure stays fast, just
sourced from the op layer instead of the engine.

### Unchanged

- `ResourceHub.from_yaml(path)`, `from_json(path)`, `instance()`,
  `set_instance(hub)`, `reset_instance()`.
- `hub.get(key)`, `hub.has(key)`, `hub.register(...)`, `hub.keys()`.
- All op classes, `@op`, `@graph`.

## Warnings — early signal at bootstrap time

Two warnings fire from `bootstrap()` / `auto()` / `from_yaml()`. Both use
the standard `warnings` module with a `ResourceHubWarning` category so
users can silence them via `warnings.filterwarnings(...)` if they really
want to.

| # | Trigger | Message | Where raised |
|---|---|---|---|
| W1 | `auto()` or `bootstrap()` called, no `resources.yaml` at CWD | `No resources.yaml found at <abs path>. ResourceHub not installed; provider ops will fail at resolution.` | `ResourceHub.auto` |
| W2 | `from_yaml()` loads a file that references `${VAR}` for an unset env var | `resources.yaml references unset environment variable: ${OPENAI_API_KEY} (used by 'llm:gpt-4o'). Resource will fail at resolution unless the variable is set before then.` | `YamlConfigStorage.__init__` (one-time scan after load) |

W2 is a **scan**, not lazy resolution: when `from_yaml` finishes loading
the YAML, it walks the raw dict once collecting all `${VAR}` references,
checks each against `os.environ`, and warns on the first missing per
`(var, used-by-key)` pair. It does not fail the load — interpolation
still happens lazily at `get()` time, which is where errors fire.

A `resources.yaml` listing ten LLMs with only `OPENAI_API_KEY` set will
warn for the other nine at bootstrap, even if the workflow only uses
OpenAI. That's the cost of early signal; users who don't want it can
scope their `resources.yaml` or filter the warning.

## Errors — five disambiguated branches

Every error message names the fix. The hub tracks enough state for
`get()` to tell which branch fired.

| # | Trigger | Message | Where raised |
|---|---|---|---|
| 1 | `ResourceHub.instance()` called, none installed | `ResourceHub not configured. Call operonx.bootstrap() or ResourceHub.from_yaml(<path>) before resolving resources.` | `ResourceHub.instance` |
| 2 | Hub installed but `source_path is None` (defensive — `auto()` returns `None` rather than installing an empty hub) | `ResourceHub has no source. Re-install via ResourceHub.from_yaml(...).` | `hub.get` when `source_path is None` |
| 3 | File loaded, key absent | `Resource 'llm:gpt-4o' not found in <source_path>.\nAvailable: <hub.keys()>.` | `hub.get` |
| 4 | Key present, `${VAR}` interpolation failed at resolve time | `Resource 'llm:gpt-4o': environment variable OPENAI_API_KEY is unset.\nLoaded from: <source_path>. .env searched: <list>.` | `YamlConfigStorage.load_one` (wrapped as `EnvVarUnsetError`) |
| 5 | Key present, env OK, factory raised | `Resource 'llm:gpt-4o' failed to initialize: <inner error>` | `hub.get` |

### State the hub tracks

- `source_path: Optional[Path]` — what `from_yaml` / `from_json` loaded.
  Used in branch (3) and (4) error messages.
- A module-level `BOOTSTRAP_ENV_PATHS: list[Path]` populated by
  `operonx.bootstrap()` when it loads `.env`. Used in branch (4) and
  warning W2.

`auto()` does not install an empty hub on miss — it returns `None` and
warns (W1). So no `searched_paths` attribute or `is_empty()` method on
storage is needed.

### Env interpolation — two-pass design

Warn early, error lazily.

**Pass 1 — load-time scan (warning W2):** when `YamlConfigStorage.__init__`
finishes parsing the file, walk the raw dict once, collect every `${VAR}`
reference and the resource key it appears under, check each against
`os.environ`, emit one `ResourceHubWarning` per missing `(var, key)` pair.
No interpolation yet.

**Pass 2 — get-time interpolation (error branch 4):** when `hub.get(key)`
triggers `_load_config`, the `${VAR}` substitution runs. If a variable is
still unset, the failure is wrapped in `EnvVarUnsetError` (a subclass of
`KeyError`, so existing `except KeyError` still catches it) with the var
name, the resource key, `source_path`, and `BOOTSTRAP_ENV_PATHS`.

A user who sets `OPENAI_API_KEY` *after* `bootstrap()` but *before*
`engine.run()` sees the W2 warning but no error — the warning is cheap
and acceptable to false-positive in this case.

## Common usage patterns

### Most projects (95%)

```python
import operonx
from operonx.core import Operon

operonx.bootstrap()              # loads ./.env + ./resources.yaml from CWD
engine = Operon(graph)
```

### Notebooks, multi-config, tests

```python
import operonx
operonx.bootstrap(resources="configs/prod.yaml")  # also loads ./.env unless env=False
```

### Pure-compute graphs

No setup at all — `Operon(graph)` works hub-free if the graph doesn't
reference any resource by name:

```python
from operonx.core import Operon, GraphOp, op, START, END, PARENT

@op
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="pure") as graph:
    step = double(x=PARENT["x"])
    START >> step >> END

result = await Operon(graph).run(inputs={"x": 5})  # no .env, no resources.yaml needed
```

### Tests

`set_instance(hub)` is **authoritative** — `bootstrap()` and `auto()` are
idempotent and respect a hub that's already installed. Provider integration
tests can pre-install a mock hub via `setup_resource_hub` autouse fixtures
without worrying about engine init clobbering it.

## Behavior reference

| Situation | What happens |
|---|---|
| `bootstrap()`, no `./resources.yaml` | `ResourceHubWarning` (W1) — pure compute still works; provider ops will fail at op resolution. |
| `bootstrap()`, `${VAR}` referenced but unset | `ResourceHubWarning` (W2) listing every unset var and the resource that uses it. Setting the var before `engine.run()` resolves it. |
| `Operon(graph)` with provider op, no hub installed | Branch (1) `RuntimeError` at engine init (eager warmup). |
| `hub.get("llm:gpt-4o")` with key not present | Branch (3) `KeyError` listing source path and available keys. |
| `hub.get(key)` with `${VAR}` still unset at resolve time | Branch (4) `EnvVarUnsetError` (subclass of `KeyError`) naming the var, source path, and `.env` paths searched. |

## Key invariants

- `Operon(graph)` does **not** load `.env` or `resources.yaml`. It does
  not clobber a pre-installed hub. It is a pure orchestrator.
- `ResourceHub.set_instance(hub)` is authoritative — `bootstrap()` and
  `auto()` are idempotent and respect a hub that's already installed.
- Run from any CWD — `bootstrap(resources="absolute/path.yaml")`
  decouples setup from working directory.

## Migration from the old auto-load behaviour

This was a **breaking change** at the API surface, with a small surface
and a mechanical migration:

- `Operon(graph, resources='...')` becomes
  `operonx.bootstrap(resources='...')` followed by `Operon(graph)`.
  No shim — the kwarg is removed.
- Code relying on `Operon(graph)` to auto-load `.env` and `resources.yaml`
  must add a `operonx.bootstrap()` call before it. Pure-compute graphs
  still work unchanged; provider graphs raise the branch (1) error.

The branch (1) message names the exact fix, so the migration cost for
downstream code is one error → one line change.

## Rust mirror

The Rust crate has the equivalent setup model. `Operon::new(graph_json)`
no longer auto-loads `.env` or `resources.yaml`. `OperonError::EnvVarUnset`
is the typed error variant for branch (4); `tracing::warn!` is the channel
for W1 / W2 (the Rust analogue of Python's `warnings` module).

Parity invariants the Rust runtime preserves:

- `Operon::new(graph_json)` works for pure-compute graphs with no
  `resources.yaml` and no `.env` anywhere — no error, no panic, at most
  one `tracing::warn!`.
- A pre-installed hub via `ResourceHub::set_instance(...)` is **not**
  clobbered by subsequent `Operon::new` / `OperonBuilder::build` calls.
- The five error branches and two warnings have the same semantics as
  Python; only the error type and warning channel differ.

See [Rust and Python](rust-python.md) for the broader parity story.

## API reference

- [`operonx.bootstrap`](../api/core.md#operonx.bootstrap) — top-level setup.
- [`ResourceHub`](../api/registry.md) — the singleton class.
- [`EnvVarUnsetError` and `ResourceHubWarning`](../api/registry.md#errors-and-warnings).
