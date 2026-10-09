"""A run started inside an op body is a step of the run that op is in.

An op body that runs a graph — an agent's tool, a helper graph a service
op runs per call — starts a run of its own (``Operon(g).run(...)``,
:func:`operonx.invoke`). That run still records into its own handle's
trace, as every run does. With :class:`NestedRun` its records also join the
caller's trace, under the step that started it, so a trace viewer opens the
step and finds the graph's ops inside:

    app.agent.turn.lookup                      main.turn[0].lookup[0]          (the tool's step)
    app.agent.turn.lookup.lookup_flow          ...lookup[0].lookup_flow[0]     (the run, op_type "graph")
    app.agent.turn.lookup.lookup_flow.fetch    ...lookup[0].lookup_flow[0]     (one of its ops)

The run's own record is shaped like a ``child()`` step of the caller (full
name and ctx each one step longer), so every reader already places it. Its
ops carry the run's full name and ctx as prefixes, and ``build_tree`` puts
a record under the nearest such run record whose full name and ctx it
extends (``nested_owner``).

What the nested run decides stays inside it: a failure in it fails the
caller only if it reaches the caller (``WorkflowTrace.status`` skips the
records under a nested run; the caller's own record says whether it
failed).
"""

from __future__ import annotations

import dataclasses
from time import perf_counter
from typing import Any, Dict, Optional, Tuple

from operonx.core.workflow_trace import (
    STATUS_CANCELLED,
    STATUS_ERROR,
    STATUS_OK,
    OpExecution,
    UpstreamRef,
    format_ctx,
    make_op_id,
)

__all__ = ["NESTED_RUN", "NestedRun"]

#: The ``op_type`` of a nested run's own record. No op records itself as a
#: graph, so it marks the record a nested run's ops hang under.
NESTED_RUN = "graph"


class NestedRun:
    """Forwards one nested run's records and task events into the caller's
    trace, renamed under the step that started it.

    Built by ``Operon.start`` when it runs inside an op body whose trace is
    live; ``record`` and ``task`` are the nested trace's listeners, and
    ``close`` writes the run's own record when it ends.
    """

    __slots__ = ("parent", "caller", "name", "full", "ctx", "prefix", "start", "inputs", "_tasks")

    def __init__(self, parent: Any, caller: Any, name: str, inputs: Dict[str, Any]) -> None:
        self.parent = parent
        self.caller = caller
        self.name = name
        counts = caller.counts
        if counts is None:
            counts = caller.counts = {}
        index = counts.get(name, 0)
        counts[name] = index + 1
        # the run's own record: one step under the caller, as child() names one
        self.full = f"{caller.full_name}.{name}"
        self.ctx: Tuple[str, ...] = caller.child_base() + (f"{name}[{index}]",)
        # every nested full name starts with the nested graph's name
        self.prefix = caller.full_name
        self.start = perf_counter()
        self.inputs = caller.op._filter_for_trace(dict(inputs or {}))
        self._tasks = bool(parent._task_listeners)

    # ── renaming ───────────────────────────────────────────────────────

    def full_of(self, full_name: str) -> str:
        return f"{self.prefix}.{full_name}" if full_name else self.full

    def ctx_of(self, ctx: Tuple[str, ...]) -> Tuple[str, ...]:
        # a nested ctx starts at the run's root ("main", ...)
        return self.ctx + tuple(ctx[1:])

    def id_of(self, op_id: Optional[str], full_name: str) -> Optional[str]:
        """``"<full>#main.<rest>[@n]"`` → the same execution's id in the
        caller's trace. Split on the known full name, never on ``#`` or
        ``.``, which a ctx segment may hold (a loop iteration's)."""
        if not op_id or not full_name or not op_id.startswith(full_name + "#"):
            return op_id
        rest = op_id[len(full_name) + 1 :]
        if rest == "main" or rest.startswith(("main.", "main@")):
            rest = rest[4:]
            return f"{self.full_of(full_name)}#{format_ctx(self.ctx)}{rest}"
        return op_id

    # ── listeners ──────────────────────────────────────────────────────

    def record(self, r: OpExecution) -> None:
        """The nested trace's execution listener: the record, renamed, into
        the caller's trace."""
        self.parent.record(
            dataclasses.replace(
                r,
                op_id=self.id_of(r.op_id, r.op_full_name),
                op_full_name=self.full_of(r.op_full_name),
                ctx=self.ctx_of(tuple(r.ctx)),
                upstreams=[
                    UpstreamRef(
                        from_op_id=self.id_of(u.from_op_id, u.from_op_full_name),
                        from_op_name=u.from_op_name,
                        from_op_full_name=self.full_of(u.from_op_full_name),
                        from_key=u.from_key,
                        to_key=u.to_key,
                    )
                    for u in r.upstreams
                ],
                inputs_from=self.id_of(r.inputs_from, r.op_full_name)
                if r.inputs_from
                else r.inputs_from,
            )
        )

    @property
    def forwards_tasks(self) -> bool:
        return self._tasks

    def task(self, event: Any) -> None:
        """The nested trace's task listener: the event, renamed, to whoever
        streams the caller's run with ``mode="tasks"``."""
        self.parent.emit_task(
            dataclasses.replace(event, op=self.full_of(event.op), ctx=self.ctx_of(tuple(event.ctx)))
        )

    def opened(self) -> None:
        if self._tasks:
            from operonx.core.runtime import TaskStarted

            self.parent.emit_task(TaskStarted(self.full, self.ctx, self.caller.attempt))

    def close(
        self, outputs: Optional[Dict[str, Any]], status: str, error: Optional[str] = None
    ) -> None:
        """Write the run's own record into the caller's trace: ``status``
        is ``ok``, ``error`` (the run failed) or ``cancelled``."""
        end = perf_counter()
        op = self.caller.op
        self.parent.record(
            OpExecution(
                op_id=make_op_id(self.full, self.ctx),
                op_name=self.name,
                op_full_name=self.full,
                ctx=self.ctx,
                start_time=self.start,
                end_time=end,
                inputs=self.inputs,
                outputs=op._filter_for_trace(dict(outputs or {})),
                status=status,
                error=error,
                op_type=NESTED_RUN,
                attempt=self.caller.attempt,
            )
        )
        if self._tasks:
            from operonx.core.runtime import TaskFailed, TaskFinished

            ms = (end - self.start) * 1000.0
            if status != STATUS_OK:
                self.parent.emit_task(
                    TaskFailed(
                        self.full,
                        self.ctx,
                        self.caller.attempt,
                        ms,
                        error=error or "",
                        cancelled=status == STATUS_CANCELLED,
                    )
                )
            else:
                self.parent.emit_task(TaskFinished(self.full, self.ctx, self.caller.attempt, ms))


def nested_owner(
    full_name: str, ctx: Tuple[str, ...], roots: Dict[str, list]
) -> Optional[Tuple[Any, int]]:
    """The nested-run record ``(record, depth)`` that a record belongs to.

    ``roots`` maps each nested run's full name to its records (op_type
    ``"graph"``). The owner is the one with the longest full name that
    prefixes ``full_name`` by dot segments and whose ctx prefixes ``ctx``.
    ``depth`` is how many full-name segments the owner has. ``None`` for a
    record of a run that started no nested run.
    """
    if not roots:
        return None
    parts = full_name.split(".")
    for k in range(len(parts) - 1, 0, -1):
        cands = roots.get(".".join(parts[:k]))
        if not cands:
            continue
        best = None
        for c in cands:
            cc = tuple(c.ctx)
            if len(cc) <= len(ctx) and tuple(ctx[: len(cc)]) == cc:
                if best is None or len(cc) > len(best.ctx):
                    best = c
        if best is not None:
            return best, k
    return None
