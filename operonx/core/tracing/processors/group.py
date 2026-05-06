"""Group-aware processors — `GroupBy` and `Aggregate`.

`GroupBy` injects synthetic ``GROUP_START`` / ``GROUP_END`` markers around
runs of events bounded by a user-defined predicate. `Aggregate` streams a
running tally over events of a target ``op_name`` inside the active group
window and emits one ``ANNOTATION`` per group.

The streaming reducer pattern (Rule 2 in
``docs/TRACING_REDESIGN_PLAN.md`` §3.6) keeps memory constant regardless
of window size — `Aggregate` never holds the per-event list, only the
reducer's running state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Optional

from operonx.core.tracing.events import EventKind, TraceEvent


# ---------------------------------------------------------------------------
# GroupBy — emit synthetic group markers at boundaries
# ---------------------------------------------------------------------------


class GroupBy:
    """Emit ``GROUP_START`` / ``GROUP_END`` events around runs of events.

    A boundary event marks the LAST event of the current group. Events
    before the first boundary belong to the implicit first group; events
    after the last boundary belong to a trailing group closed at stream
    end with ``status="truncated"``.

    Args:
        boundary: predicate ``(event) -> bool``. True marks group end.
        name_fn:  ``(group_index) -> str``. Defaults to ``"group-N"``.
    """

    __slots__ = ("_boundary", "_name_fn")

    def __init__(
        self,
        boundary: Callable[[TraceEvent], bool],
        name_fn: Optional[Callable[[int], str]] = None,
    ) -> None:
        self._boundary = boundary
        self._name_fn = name_fn or (lambda i: f"group-{i}")

    def __call__(self, events: Iterable[TraceEvent]) -> Iterable[TraceEvent]:
        boundary = self._boundary
        name_fn = self._name_fn

        group_idx = 0
        current_name: Optional[str] = None
        last_event: Optional[TraceEvent] = None

        for event in events:
            # Open a new group lazily — only when the first real event arrives.
            if current_name is None:
                current_name = name_fn(group_idx)
                group_idx += 1
                yield _make_group_start(event, current_name)

            yield event
            last_event = event

            # Boundary event closes the current group. The next non-meta event
            # will lazily open the next group.
            if boundary(event):
                yield _make_group_end(event, current_name, status="ok")
                current_name = None

        # Trailing group: stream ended without hitting a boundary.
        if current_name is not None and last_event is not None:
            yield _make_group_end(last_event, current_name, status="truncated")


# ---------------------------------------------------------------------------
# Aggregate — streaming reducer over events inside a group window
# ---------------------------------------------------------------------------


class Aggregate:
    """Fold events of a target ``op_name`` into a running summary.

    Consumes (drops) events whose ``op_name`` matches inside an active
    group window — emits one synthetic ``ANNOTATION`` event before each
    matching ``GROUP_END`` carrying the reducer's final state.

    Memory cost is the size of one ``state`` dict — independent of window
    size. Operations that need the full event list (median, percentiles,
    top-K) are not supported in v1.

    Args:
        op_name:  the op whose events to aggregate.
        window:   ``"group"`` (only mode in v1).
        init:     ``() -> state`` — fresh accumulator at each GROUP_START.
        reduce:   ``(state, event) -> state`` — fold one event in.
        emit:     ``state -> dict`` — payload for the synthetic ANNOTATION.

    The annotation's payload is ``{"key": "summary:{op_name}", "value": <emit_result>}``.
    """

    __slots__ = ("_op_name", "_window", "_init", "_reduce", "_emit")

    def __init__(
        self,
        op_name: str,
        window: str = "group",
        init: Optional[Callable[[], Any]] = None,
        reduce: Optional[Callable[[Any, TraceEvent], Any]] = None,
        emit: Optional[Callable[[Any], Dict[str, Any]]] = None,
    ) -> None:
        if window != "group":
            raise NotImplementedError(
                f"Aggregate only supports window='group' in v1 (got {window!r})"
            )
        self._op_name = op_name
        self._window = window
        self._init = init or (lambda: {})
        self._reduce = reduce or (lambda s, _e: s)
        self._emit = emit or (lambda s: dict(s) if isinstance(s, dict) else {"value": s})

    def __call__(self, events: Iterable[TraceEvent]) -> Iterable[TraceEvent]:
        op_name = self._op_name
        init = self._init
        reduce = self._reduce
        emit = self._emit

        active_state: Any = None
        active_group_event: Optional[TraceEvent] = None

        for event in events:
            if event.kind is EventKind.GROUP_START:
                # Start a fresh accumulator for this window.
                active_state = init()
                active_group_event = event
                yield event
                continue

            if event.kind is EventKind.GROUP_END:
                # Emit the synthetic ANNOTATION with the summary, THEN the GROUP_END.
                if active_state is not None and active_group_event is not None:
                    yield _make_summary_annotation(
                        active_group_event,
                        op_name,
                        emit(active_state),
                    )
                active_state = None
                active_group_event = None
                yield event
                continue

            # Inside an active window, target events are consumed (dropped).
            # Outside any window OR non-target events pass through.
            if active_state is not None and _name_matches(event.op_name, op_name):
                active_state = reduce(active_state, event)
                continue

            yield event


# ---------------------------------------------------------------------------
# Synthetic event helpers
# ---------------------------------------------------------------------------


def _make_group_start(event: TraceEvent, name: str) -> TraceEvent:
    """Synthesize a GROUP_START event sourced from ``event``'s metadata."""
    return TraceEvent(
        event_id=f"{event.event_id}-grp-start",
        request_id=event.request_id,
        kind=EventKind.GROUP_START,
        op_name=None,
        ctx=(),
        timestamp=event.timestamp,
        seq=event.seq,
        payload={"name": name},
    )


def _make_group_end(event: TraceEvent, name: str, *, status: str) -> TraceEvent:
    """Synthesize a GROUP_END event sourced from ``event``'s metadata."""
    return TraceEvent(
        event_id=f"{event.event_id}-grp-end",
        request_id=event.request_id,
        kind=EventKind.GROUP_END,
        op_name=None,
        ctx=(),
        timestamp=event.timestamp,
        seq=event.seq + 1,
        payload={"name": name, "status": status},
    )


def _make_summary_annotation(
    group_start_event: TraceEvent,
    op_name: str,
    summary: Dict[str, Any],
) -> TraceEvent:
    """Synthesize an ANNOTATION carrying the aggregator's final state.

    Sequenced just after the group_start so it sorts before subsequent
    events in the window when timestamps tie.
    """
    return TraceEvent(
        event_id=f"{group_start_event.event_id}-summary",
        request_id=group_start_event.request_id,
        kind=EventKind.ANNOTATION,
        op_name=None,
        ctx=group_start_event.ctx,
        timestamp=group_start_event.timestamp,
        seq=group_start_event.seq + 1,
        payload={"key": f"summary:{op_name}", "value": summary},
    )


def _name_matches(event_op_name: Optional[str], target: str) -> bool:
    """Match either fully-qualified name or short name (``"g.audio"`` vs ``"audio"``)."""
    if event_op_name is None:
        return False
    if event_op_name == target:
        return True
    return event_op_name.rsplit(".", 1)[-1] == target
