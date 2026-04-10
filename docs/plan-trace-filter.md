# Plan: TraceFilter — Configurable Trace Filtering for Hush-ai

**Date:** 2026-04-10
**Scope:** hush-icore + hush-telemetry
**Goal:** Allow users to control which ops get traced, reducing noise in Langfuse/OTEL without code changes.

---

## Problem

Every `@op` execution creates a Langfuse span — even ops that yield nothing (e.g., `recv_audio` firing 100x with no speech detected). In a streaming callbot workflow, a single call produces hundreds of low-value spans that drown out the useful ones (LLM generations, intent classification, TTS).

The only existing filter (`skip_pending`) removes generators with `yield_count==0`, but does not cover:
- Batch ops with all-null outputs
- Specific ops by name (e.g., `recv_audio`, `denoise`, `vad_detect`)
- Filtering by node kind (e.g., drop all `stream_context` synthetic nodes)

---

## Design

### TraceFilter dataclass (new file in hush-icore)

```python
@dataclass
class TraceFilter:
    skip_empty: bool = False
    exclude_ops: list[str] = field(default_factory=list)
    include_ops: list[str] = field(default_factory=list)
    exclude_kinds: list[str] = field(default_factory=list)
    protected_types: list[str] = field(default_factory=lambda: ["trace", "generation"])
```

- `skip_empty` — drop nodes where ALL output values are None
- `exclude_ops` — drop nodes matching these op names
- `include_ops` — if non-empty, only keep nodes matching these op names (whitelist mode)
- `exclude_kinds` — drop nodes by kind (`batch`, `generator`, `stream_context`, `loop_iter`, `graph`)
- `protected_types` — node_types that are NEVER filtered out (default: `trace` + `generation`)

`exclude_ops` and `include_ops` are mutually exclusive — raise `ValueError` if both are non-empty.

### Op name matching strategy

Match against **both** `display_name` (short: `"recv_audio"`) and `op_name` (full: `"callbot.recv_audio"`). A filter entry matches if it equals either one. This lets users write simple short names in YAML while still allowing unambiguous full names for nested graphs with name collisions.

### Orphan handling: re-parent, not cascade-delete

**Critical:** When a node is filtered out, its children must not become orphans (Langfuse silently re-parents orphans to the root trace, corrupting hierarchy). But cascade-deleting an entire subtree is too aggressive — the user wants to hide `recv_audio` but keep its child ops visible.

**Strategy: re-parent children to the filtered node's parent.**

```
Before filter (exclude recv_audio):
  callbot (trace)
  └── [0] (stream_context)
      ├── recv_audio (batch)      ← filtered out
      │   └── denoise (batch)     ← child must not be orphaned
      └── classify (generation)

After filter:
  callbot (trace)
  └── [0] (stream_context)
      ├── denoise (batch)         ← re-parented to [0]
      └── classify (generation)
```

Exception: if `skip_empty=True` filters a synthetic `stream_context` that has no remaining children after filtering, remove it entirely (same as existing `_remove_pending` logic).

### YAML-driven configuration

Users configure filtering in `resources.yaml` under the langfuse config:

```yaml
langfuse:
  default:
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
    host: ${LANGFUSE_BASE_URL}
    trace_filter:
      skip_empty: true
      exclude_ops:
        - recv_audio
        - denoise
```

The `trace_filter` key is parsed into a `TraceFilter` instance when the tracer is constructed from a resource.

### Python API (programmatic override)

```python
tracer = LangfuseTracer(
    resource="langfuse:default",
    trace_filter=TraceFilter(skip_empty=True, exclude_ops=["recv_audio"]),
)
```

If both YAML config and constructor provide `trace_filter`, constructor wins (explicit override).

---

## Verification Findings

Before designing this plan, the following assumptions were verified against the codebase:

| # | Assumption | Status | Finding |
|---|-----------|--------|---------|
| 1 | Node dict has `display_name`, `op_name`, `node_type`, `kind`, `outputs` keys | **Verified** | TraceNode has 15 fields, all present after `_safe_asdict()` (models.py:24-58) |
| 2 | Removing a node orphans its children in Langfuse | **Confirmed danger** | `_set_parent()` silently skips missing parents → children become root-level (langfuse.py:66-74) |
| 3 | Nodes are list of dicts when passed to tracer | **Verified** | `_safe_asdict()` runs at collect time (collector.py:185), each tracer gets a `{**trace_data}` copy (flush_worker.py:82) |
| 4 | Constructor chain: LangfuseTracer → ConfigurableTracer → Tracer | **Verified** | `tags` flows through all 3; `stream_trace_limit` set at Tracer level only |
| 5 | Resource-based tracer does NOT have access to raw YAML config | **Confirmed** | `_get_client()` returns instantiated `LangfuseClient`, not config dict (_base.py:45-51) |
| 6 | `_sample_stream_nodes` does cascading child removal | **Verified** | BFS removal of all descendants (flush_worker.py:159-173) |
| 7 | `display_name` = short name, `op_name` = full dotted name | **Verified** | collector.py:129-138 |
| 8 | `__init__.py` exports need updating | **Verified** | Current exports at tracing/__init__.py:22-30 |

### Key risk from Check 5

Resource-based tracers (`LangfuseTracer(resource="langfuse:default")`) only get the instantiated client — they never see the raw YAML dict. To read `trace_filter` from YAML, the `ConfigurableTracer` must explicitly load the config from ResourceHub and parse it. This requires a new method in `ConfigurableTracer`.

---

## Implementation Steps

### Step 1: Create `TraceFilter` dataclass

**File:** `python/hush-icore/hush/core/tracing/trace_filter.py` (NEW)

```python
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class TraceFilter:
    skip_empty: bool = False
    exclude_ops: List[str] = field(default_factory=list)
    include_ops: List[str] = field(default_factory=list)
    exclude_kinds: List[str] = field(default_factory=list)
    protected_types: List[str] = field(default_factory=lambda: ["trace", "generation"])

    def __post_init__(self):
        if self.exclude_ops and self.include_ops:
            raise ValueError("Cannot set both exclude_ops and include_ops")

    def apply(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter nodes and re-parent orphaned children."""
        ...

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TraceFilter":
        """Parse from YAML dict. Unknown keys are ignored."""
        ...
```

**`apply()` algorithm:**

```
1. Build set of trace_keys to remove.
2. For each node:
   a. If node_type in protected_types → KEEP (never filter trace/generation)
   b. If op matches exclude_ops → REMOVE
   c. If include_ops is set and op does NOT match → REMOVE
   d. If kind in exclude_kinds → REMOVE
   e. If skip_empty and all output values are None → REMOVE
3. Re-parent pass: for each remaining node whose parent_trace_key is in remove set,
   walk up the removed node's parent chain until finding a surviving ancestor.
   Set node's parent_trace_key to that ancestor.
4. Cleanup pass: remove synthetic stream_context/loop_iter nodes that have
   no remaining children (same as _remove_pending cascade logic).
5. Return filtered list.
```

**Op matching helper** (used in steps 2b, 2c):

```python
def _op_matches(self, node: dict, op_list: list) -> bool:
    """True if node's display_name or op_name matches any entry in op_list."""
    dn = node.get("display_name", "")
    on = node.get("op_name", "")
    return dn in op_list or on in op_list
```

**Empty output check** (used in step 2e):

```python
def _is_empty_output(node: dict) -> bool:
    outputs = node.get("outputs")
    if not outputs:
        return True
    return all(v is None for v in outputs.values())
```

### Step 2: Add `trace_filter` to `Tracer` base class

**File:** `python/hush-icore/hush/core/tracing/base.py`

**Change:** Add `trace_filter` parameter to `__init__`:

```python
def __init__(
    self,
    tags: Optional[List[str]] = None,
    stream_trace_limit: Optional[int] = 100,
    trace_filter: Optional["TraceFilter"] = None,    # NEW
):
    self._tags = tags or []
    self._stream_trace_limit = stream_trace_limit
    self._trace_filter = trace_filter                 # NEW

@property
def trace_filter(self) -> Optional["TraceFilter"]:   # NEW
    return self._trace_filter
```

### Step 3: Apply filter in `FlushWorker`

**File:** `python/hush-icore/hush/core/tracing/flush_worker.py`

**Change:** In `_collect_and_flush()`, apply `trace_filter` after `_sample_stream_nodes` but before `tracer.flush()`:

```python
# Existing line 79-80:
limit = getattr(tracer, "_stream_trace_limit", None)
sampled_nodes = _sample_stream_nodes(nodes, limit)

# NEW: apply trace_filter
tf = getattr(tracer, "trace_filter", None)
filtered_nodes = tf.apply(sampled_nodes) if tf else sampled_nodes

# Existing line 82 (modified):
data = {**trace_data, "tags": merged if merged else None, "nodes": filtered_nodes}
```

**Why here and not in the tracer?** This keeps filtering in one place, before any backend-specific logic. Each tracer can have a different filter, and the FlushWorker already handles per-tracer sampling (`_sample_stream_nodes`), so this is the natural extension point.

### Step 4: Thread `trace_filter` through `ConfigurableTracer`

**File:** `python/hush-telemetry/hush/telemetry/tracers/_base.py`

**Change:** Accept `trace_filter` in constructor and pass to super:

```python
def __init__(
    self,
    config=None,
    resource: Optional[str] = None,
    tags: Optional[List[str]] = None,
    trace_filter: Optional["TraceFilter"] = None,     # NEW
):
    super().__init__(tags=tags, trace_filter=trace_filter)
    ...
```

### Step 5: Parse `trace_filter` from YAML config in `ConfigurableTracer`

**File:** `python/hush-telemetry/hush/telemetry/tracers/_base.py`

When using resource-based init (`resource="langfuse:default"`), the tracer calls `get_hub().get(resource)` which returns a `LangfuseClient` — the raw config with `trace_filter` key is lost. We need to load the raw config separately.

**Change:** Add a method to load `trace_filter` from ResourceHub config:

```python
def _load_trace_filter_from_resource(self) -> Optional["TraceFilter"]:
    """Load trace_filter from the resource's raw YAML config."""
    if not self._resource:
        return None
    from hush.core.registry import get_hub
    try:
        config = get_hub().get_config(self._resource)
    except KeyError:
        return None
    # config is a YamlModel or raw dict
    raw = config if isinstance(config, dict) else config.model_dump()
    tf_dict = raw.get("trace_filter")
    if not tf_dict:
        return None
    from hush.core.tracing.trace_filter import TraceFilter
    return TraceFilter.from_dict(tf_dict)
```

**Call it in `__init__`:** If no explicit `trace_filter` was passed, try loading from resource config:

```python
def __init__(self, config=None, resource=None, tags=None, trace_filter=None):
    super().__init__(tags=tags, trace_filter=trace_filter)
    ...
    # Auto-load trace_filter from YAML if not explicitly provided
    if self._trace_filter is None and resource is not None:
        self._trace_filter = self._load_trace_filter_from_resource()
```

**Potential issue:** `get_hub()` may not be initialized yet when the tracer is constructed. In the callbot, `ResourceHub.set_instance()` runs before tracer creation, so this is safe. But for safety, wrap in try/except and log a warning if hub is not ready.

### Step 6: Add `trace_filter` field to `LangfuseConfig`

**File:** `python/hush-telemetry/hush/telemetry/backends/langfuse/config.py`

**Change:** Add optional `trace_filter` dict field so YAML parsing doesn't reject the key:

```python
class LangfuseConfig(YamlModel):
    public_key: str
    secret_key: str
    host: str = "https://cloud.langfuse.com"
    no_proxy: Optional[str] = None
    enabled: bool = True
    sample_rate: float = 1.0
    trace_filter: Optional[dict] = None    # NEW — raw dict, parsed by tracer
```

This is intentionally `dict` not `TraceFilter` — `LangfuseConfig` lives in hush-telemetry and only stores the raw YAML. The parsing into `TraceFilter` happens in Step 5.

**Wait — LangfuseConfig is a YamlModel (Pydantic).** Unknown fields are rejected by default. We MUST add this field or YAML parsing will fail when users add `trace_filter` to their config. Verified: YamlModel likely inherits Pydantic's strict validation.

### Step 7: Update `__init__.py` exports

**File:** `python/hush-icore/hush/core/tracing/__init__.py`

```python
from hush.core.tracing.trace_filter import TraceFilter

__all__ = [
    ...
    "TraceFilter",
]
```

### Step 8: Thread `trace_filter` through `LangfuseTracer` and `OTELTracer`

**File:** `python/hush-telemetry/hush/telemetry/tracers/langfuse.py`

```python
def __init__(
    self,
    config: Optional["LangfuseConfig"] = None,
    resource: Optional[str] = None,
    tags: Optional[List[str]] = None,
    trace_filter: Optional["TraceFilter"] = None,     # NEW
):
    super().__init__(config=config, resource=resource, tags=tags, trace_filter=trace_filter)
```

Same for `OTELTracer` if it exists.

### Step 9: Tests

**File:** `python/hush-icore/tests/tracing/test_trace_filter.py` (NEW)

Test cases:

| # | Test | Description |
|---|------|-------------|
| 1 | `test_no_filter` | Empty TraceFilter passes all nodes through |
| 2 | `test_skip_empty` | Nodes with all-None outputs are removed |
| 3 | `test_skip_empty_preserves_generation` | `node_type="generation"` with None outputs kept |
| 4 | `test_exclude_ops_short_name` | `exclude_ops=["recv_audio"]` matches `display_name` |
| 5 | `test_exclude_ops_full_name` | `exclude_ops=["callbot.recv_audio"]` matches `op_name` |
| 6 | `test_include_ops_whitelist` | Only listed ops survive |
| 7 | `test_include_ops_preserves_protected` | `node_type="trace"` survives even if not in include list |
| 8 | `test_exclude_kinds` | `exclude_kinds=["stream_context"]` removes synthetic nodes |
| 9 | `test_reparent_on_removal` | Children of removed node get re-parented to grandparent |
| 10 | `test_reparent_chain` | If parent AND grandparent both removed, child re-parents to great-grandparent |
| 11 | `test_empty_context_cleanup` | Synthetic nodes with no remaining children are removed |
| 12 | `test_mutual_exclusion` | `exclude_ops` + `include_ops` both set → `ValueError` |
| 13 | `test_from_dict` | Parse from YAML-like dict |
| 14 | `test_from_dict_unknown_keys` | Unknown keys in dict are ignored (forward compat) |

### Step 10: Version bump + changelog

- Bump `hush-icore` version (e.g., 0.4.4 → 0.4.5)
- Bump `hush-telemetry` version
- Update educa-reminder-agent `pyproject.toml` to require new versions

---

## Execution Order and Dependencies

```
Step 1  ──→  Step 2  ──→  Step 3      (hush-icore: filter → base → flush_worker)
                │
                ↓
Step 7  (update __init__.py exports)
                │
                ↓
Step 6  ──→  Step 4  ──→  Step 5  ──→  Step 8   (hush-telemetry: config → base → parse → tracers)
                │
                ↓
             Step 9  (tests — can run after Steps 1-3)
                │
                ↓
            Step 10  (version bump — last)
```

Steps 1-3 + 7 can be done together (pure hush-icore, no telemetry dependency).
Steps 4-6 + 8 done after (hush-telemetry depends on hush-icore changes).

---

## Edge Cases and Risks

| Risk | Mitigation |
|------|-----------|
| Re-parent target also filtered → child orphaned | Step 3 of `apply()` walks the full ancestor chain until a surviving node is found. If none found, re-parent to `None` (becomes direct trace child) |
| `include_ops` accidentally excludes everything | `protected_types` ensures trace root + LLM generations always survive |
| Resource hub not initialized when tracer reads config | Step 5 wraps in try/except, falls back to no filter + warning log |
| `LangfuseConfig` rejects unknown `trace_filter` field | Step 6 adds the field explicitly as `Optional[dict]` |
| Performance: filtering thousands of nodes | `apply()` is O(N) with a single dict lookup per node — negligible vs. the HTTP POST to Langfuse |
| `_safe_asdict` converts dataclass outputs to dicts — filter checks `all(v is None)` | Works correctly: `_safe_asdict` preserves None values, doesn't convert them |
| Synthetic nodes (`op_name=None`) in `exclude_ops` check | `_op_matches` returns False when `op_name` is None and `display_name` is "[0]" — synthetics pass through exclude_ops naturally |
| `skip_empty` removes an op whose children have real output | Re-parent ensures children survive. Only the empty parent span is removed |

---

## Usage Example (educa-reminder-agent)

```yaml
# resources.yaml
langfuse:
  default:
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
    host: ${LANGFUSE_BASE_URL}
    trace_filter:
      skip_empty: true
      exclude_ops:
        - recv_audio
        - denoise
        - vad_detect
```

No Python code changes needed — the existing `LangfuseTracer(resource="langfuse:default")` auto-loads the filter.