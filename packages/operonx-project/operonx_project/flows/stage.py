"""What the milestone's stage flows share: a coder call, checkpointed, its cost on the milestone.

The stage flows (``discover_flow``, ``plan_flow``, ``final_flow``) are
journalled like ``task_flow``. A stage op re-run after a crash finds the
coder sessions that had ended under its idempotency key and does not pay
for them again; the milestone's records (survey, baseline, plan) live in
the store's milestone records (:meth:`~operonx_project.store.Store.ms_put`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from operonx import child, run_context

from operonx_project.coder.base import CoderRequest, CoderResult, Started
from operonx_project.runtime import Runtime

__all__ = ["stage_call", "stage_key", "stage_request"]


def stage_key(name: str) -> str:
    rc = run_context()
    return f"{rc.idempotency_key if rc else 'nokey'}:{name}"


def stage_request(rt: Runtime, mid: str, role: str, model: str, prompt: str, cwd: Path, name: str,
                  schema: Optional[Dict[str, Any]] = None, session: Optional[str] = None,
                  add_dirs: Optional[List[Path]] = None) -> CoderRequest:  # fmt: skip
    d = Path(rt.data_dir) / "transcripts" / mid / "_milestone"
    d.mkdir(parents=True, exist_ok=True)
    return CoderRequest(
        prompt=prompt,
        cwd=Path(cwd),
        role=role,
        session=session,
        schema=schema,
        model=rt.models.get(model),
        max_usd=rt.call_max_usd,
        timeout_s=rt.call_timeout_s,
        transcript=d / f"{name}.jsonl",
        sandbox=False,  # the stage roles read; none has a shell to sandbox
        hide=[rt.heldout_root],
        add_dirs=list(add_dirs or []),
        tags={"milestone": mid, "step": name},
    )


async def stage_call(rt: Runtime, mid: str, name: str, req: CoderRequest, key: Optional[str] = None) -> CoderResult:
    """One session as a ``child()`` step. Its result is saved under the op's
    idempotency key (or *key*, outside a flow), and a re-run returns it
    without a call."""
    key = key or stage_key(name)
    saved = rt.store.checkpoint(key)
    if saved is not None:
        return CoderResult(**{**saved, "cost_usd": 0.0})

    def started(s: Started) -> None:
        rt.store.ms_put(mid, f"session:{name}", {"session": s.session, "pgid": s.pgid})

    async with child(name, inputs={"role": req.role, "session": req.session, "prompt": req.prompt[-4000:]},
                     op_type="coder") as step:  # fmt: skip
        res = await rt.coder.run(req, started)
        step.outputs = {"status": res.status, "session": res.session, "output": res.output, "error": res.error}
        step.attrs.update({"cost_usd": res.cost_usd, "turns": res.turns, "model": req.model or ""})
    if res.cost_usd:
        rt.store.add_cost(mid, res.cost_usd)
    if res.ok:
        rt.store.save_checkpoint(key, {"status": res.status, "session": res.session, "text": res.text,
                                       "output": res.output, "error": res.error})  # fmt: skip
    rt.store.note(mid, "stage.call", {"step": name, "status": res.status, "cost_usd": res.cost_usd,
                                      "error": res.error[:300]})  # fmt: skip
    return res
