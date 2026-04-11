"""Labeled iteration wrappers — let async-gen ops name their own yields.

Usage (from inside an async-gen ``@op``):

    from hush.core.tracing import label

    @op
    async def frame_source(queue):
        idx = 0
        while True:
            frame = await queue.get()
            label(f"turn-{idx} ({frame['kind']})")
            yield frame
            idx += 1

After the call, the collector uses ``"turn-0 (audio)"`` etc. as the
``display_name`` for the ``stream_context`` wrapper that Hush would
otherwise label ``"[0]"``, and emits them with ``kind="labeled_iter"``
so ``TraceFilter.exclude_kinds=["stream_context"]`` does not collapse
them.

Scope: labels are stored on the ``MemoryState`` of the run that called
``label()``. Different concurrent runs (``engine.run()`` calls) get
independent stores, so CCU workloads don't cross-contaminate. The
scheduler binds the active store via a ``ContextVar`` at the top of
each ``_run_once``.

``label()`` outside a generator op (or outside any Hush run) is a no-op.
"""

import contextvars
from typing import Any, Dict, List, Optional, Tuple

# Bound by the scheduler's _pump at the top of each op execution.
# Each async task inherits its own copy at create time, so concurrent
# ops within a single run see independent bindings.
_current_gen_key: contextvars.ContextVar[Optional[Tuple[str, tuple]]] = contextvars.ContextVar(
    "hush_current_gen_key", default=None
)

# Bound by the scheduler's _run_once for the duration of the run.
# Points to the State._iter_labels dict of the executing call, so
# multiple concurrent engine.run() invocations get independent stores.
_current_labels_store: contextvars.ContextVar[Optional[Dict[Tuple[str, tuple], Dict[str, Any]]]] = (
    contextvars.ContextVar("hush_current_labels_store", default=None)
)


def _set_gen_key(op_full_name: str, ctx: tuple) -> contextvars.Token:
    """Bind the currently-running generator op for label() resolution."""
    return _current_gen_key.set((op_full_name, ctx))


def _reset_gen_key(token: contextvars.Token) -> None:
    _current_gen_key.reset(token)


def _set_labels_store(
    store: Dict[Tuple[str, tuple], Dict[str, Any]],
) -> contextvars.Token:
    """Bind the labels store (usually ``state._iter_labels``) for this run."""
    return _current_labels_store.set(store)


def _reset_labels_store(token: contextvars.Token) -> None:
    _current_labels_store.reset(token)


def label(name: str) -> None:
    """Label the *next* yield of the currently-running async-gen op.

    No-op if called outside a generator op's execution context, or
    outside any Hush run. Safe to call from anywhere.

    Multiple calls before a single yield: the last one wins (each call
    overwrites the label for the upcoming yield). After the yield, the
    scheduler advances the "next-yield" index so subsequent label() calls
    write to the new slot.
    """
    key = _current_gen_key.get()
    store = _current_labels_store.get()
    if key is None or store is None:
        return
    entry = store.setdefault(key, {"labels": [], "next_idx": 0})
    labels: List[str] = entry["labels"]
    idx: int = entry["next_idx"]
    # Pad with empty strings so labels[idx] is always assignable.
    while len(labels) <= idx:
        labels.append("")
    labels[idx] = name


def _advance_yield(op_full_name: str, ctx: tuple) -> None:
    """Advance the next-yield index after the scheduler observes a yield.

    Called by the scheduler after each Frame is emitted from a generator
    op. If the just-yielded frame had no label set, its slot stays as
    ``""`` (collector falls back to ``[N]``).
    """
    store = _current_labels_store.get()
    if store is None:
        return
    entry = store.get((op_full_name, ctx))
    if entry is None:
        # No label() calls happened for this gen yet — nothing to track.
        # We still need to record that a yield occurred so later label()
        # calls go to the right slot.
        store[(op_full_name, ctx)] = {"labels": [""], "next_idx": 1}
        return
    entry["next_idx"] += 1


def get_labels(
    store: Optional[Dict[Tuple[str, tuple], Dict[str, Any]]],
    op_full_name: str,
    ctx: tuple,
) -> List[str]:
    """Read labels for a generator from a store. Public for the collector."""
    if not store:
        return []
    entry = store.get((op_full_name, ctx))
    return entry["labels"] if entry else []
